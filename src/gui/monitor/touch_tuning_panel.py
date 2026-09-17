"""TouchTuningPanel - live tuning for a skin's quadrant touch detection.

Shown under the SkinGridView when a skin has 4-sensor touch tracking. Lets the
operator adjust the per-quadrant detection thresholds + hysteresis while the
activity runs (applied immediately to the skin's QuadrantDetector), re-zero the
magnetic sensors on the node over ESP-NOW, and toggle the node's adaptive
baseline.

The layout lives in ``src/gui/ui_touch_tuning_panel.ui`` (edit it in Qt Designer
and recompile with ``scripts/compile_ui.sh``). This module keeps only the wiring
+ behaviour. Threshold changes are saved to the user's SoftEdIBO settings and
restored for the same touch node on the next session.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QDialog, QDoubleSpinBox, QGridLayout, QGroupBox,
                               QLabel, QPushButton, QProgressBar, QWidget)

from src.gui.ui_touch_tuning_panel import Ui_TouchTuningPanel
from src.hardware.skin import Skin


class LiveSensorWindow(QDialog):
    """Live per-sensor readout for a skin's magnet stream."""

    def __init__(self, skin: Skin, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Live touch sensors - {skin.skin_id}")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._skin = skin
        self._value_labels: list[QLabel] = []
        self._state_labels: list[QLabel] = []
        self._bars: list[QProgressBar] = []
        self._frequency_labels: list[QLabel] = []

        layout = QGridLayout(self)
        layout.addWidget(QLabel("Sensor"), 0, 0)
        layout.addWidget(QLabel("Magnitude"), 0, 1)
        layout.addWidget(QLabel("Level"), 0, 2)
        layout.addWidget(QLabel("State"), 0, 3)
        layout.addWidget(QLabel("Frequency"), 0, 4)
        for index in range(4):
            layout.addWidget(QLabel(f"Q{index + 1}"), index + 1, 0)
            value = QLabel("-- uT")
            layout.addWidget(value, index + 1, 1)
            bar = QProgressBar()
            bar.setRange(0, 2000)
            bar.setTextVisible(False)
            layout.addWidget(bar, index + 1, 2)
            state = QLabel("inactive")
            layout.addWidget(state, index + 1, 3)
            frequency = QLabel("-- Hz")
            layout.addWidget(frequency, index + 1, 4)
            self._value_labels.append(value)
            self._bars.append(bar)
            self._state_labels.append(state)
            self._frequency_labels.append(frequency)
        self.resize(560, 190)

    def update_data(self, data: dict) -> None:
        magnitudes = data.get("mag")
        active = {int(value) for value in (data.get("act") or [])
                  if str(value).lstrip("-").isdigit()}
        thresholds = self._skin.touch_thresholds or [100.0] * 4
        frequencies = getattr(self.parent(), "_frequency_hz", {})
        if not isinstance(magnitudes, (list, tuple)):
            return
        for index in range(4):
            magnitude = float(magnitudes[index]) if index < len(magnitudes) else 0.0
            threshold = float(thresholds[index]) if index < len(thresholds) else 100.0
            self._value_labels[index].setText(f"{magnitude:.1f} uT")
            self._bars[index].setRange(0, max(200, int(threshold * 3)))
            self._bars[index].setValue(min(int(magnitude), self._bars[index].maximum()))
            is_active = index in active
            self._state_labels[index].setText("ACTIVE" if is_active else "inactive")
            frequency = frequencies.get(index)
            self._frequency_labels[index].setText(
                f"{frequency:.2f} Hz" if frequency is not None else "-- Hz")


class TouchTuningPanel(QGroupBox, Ui_TouchTuningPanel):
    """Per-quadrant threshold/hysteresis tuning + sensor re-zero for a skin."""

    _live_data = Signal(object)

    def __init__(self, skin: Skin, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setupUi(self)
        self._skin = skin
        self._frequency_hz: dict[int, float] = {}
        self._last_press_ms: dict[int, float] = {}
        self._active_sensors: set[int] = set()
        self._frequency_reset_ms = self._saved_frequency_reset_ms()
        self._live_window: LiveSensorWindow | None = None

        # Compact styling so the panel stays small under the skin grid view.
        # Applied in code (not the .ui) because it is relative to the inherited
        # point size; the groupbox title keeps the normal size.
        compact = self.font()
        compact.setPointSizeF(max(7.0, compact.pointSizeF() - 1.0))
        for child in self.findChildren(QWidget):
            child.setFont(compact)

        # Quadrant thresholds (index 0..3 = Q1..Q4), seeded from the skin.
        self._threshold_spins = [self.thr0, self.thr1, self.thr2, self.thr3]
        thresholds = skin.touch_thresholds or [100.0, 100.0, 100.0, 100.0]
        for i, spin in enumerate(self._threshold_spins):
            spin.setValue(float(thresholds[i]) if i < len(thresholds) else 100.0)
            spin.valueChanged.connect(self._apply_thresholds)

        hysteresis = skin.touch_hysteresis if skin.touch_hysteresis is not None else 20.0
        self.hyst_spin.setValue(float(hysteresis))
        self.hyst_spin.valueChanged.connect(self._apply_hysteresis)

        self.spike_spin.setValue(self._saved_spike_threshold())
        self.spike_spin.valueChanged.connect(self._apply_spike_threshold)
        self.frequency_reset_spin.setValue(self._frequency_reset_ms)
        self.frequency_reset_spin.valueChanged.connect(self._apply_frequency_reset)
        self.sync_tolerance_spin.setValue(self._saved_sync_tolerance_ms())
        self.sync_tolerance_spin.valueChanged.connect(self._apply_sync_tolerance)

        self.apply_btn.clicked.connect(self._apply_node_config)
        self.rebaseline_btn.clicked.connect(self._rebaseline)
        self.adaptive_chk.toggled.connect(self._apply_adaptive_baseline)
        self.tau_spin.valueChanged.connect(self._apply_adaptive_baseline)

        self.live_btn = QPushButton("Live sensor data", self)
        self.live_btn.setToolTip("Open a live readout of the four touch sensors")
        self.bottom_row.addWidget(self.live_btn)
        self.live_btn.clicked.connect(self._show_live_window)
        self._live_data.connect(self._update_live_data,
                                Qt.ConnectionType.QueuedConnection)
        controller = getattr(skin, "touch_controller", None)
        if controller is not None and hasattr(controller, "on_magnet"):
            controller.on_magnet(lambda data: self._live_data.emit(data))

    # ------------------------------------------------------------------

    def _show_live_window(self) -> None:
        if self._live_window is None:
            self._live_window = LiveSensorWindow(self._skin, self)
        self._live_window.show()
        self._live_window.raise_()
        self._live_window.activateWindow()

    def _update_live_data(self, data: dict) -> None:
        active = data.get("act") or []
        if not isinstance(active, list):
            return
        timestamp = time.monotonic() * 1000.0
        current = {int(value) for value in active
                   if str(value).lstrip("-").isdigit()}
        for index in current - self._active_sensors:
            previous = self._last_press_ms.get(index)
            if previous is not None and timestamp > previous:
                self._frequency_hz[index] = 1000.0 / (timestamp - previous)
            self._last_press_ms[index] = timestamp
        self._active_sensors = current
        stale_ms = self._frequency_reset_ms
        self._frequency_hz = {
            index: frequency for index, frequency in self._frequency_hz.items()
            if timestamp - self._last_press_ms.get(index, 0.0) < stale_ms
        }
        if self._live_window is not None:
            self._live_window.update_data(data)

    def _apply_thresholds(self) -> None:
        thresholds = [s.value() for s in self._threshold_spins]
        self._skin.set_touch_thresholds(thresholds)
        from src.config.settings import Settings
        touch = getattr(self._skin, "touch", None) or {}
        key = str(touch.get("node_mac") or getattr(self._skin, "skin_type", ""))
        Settings().set_touch_quadrant_thresholds(key, thresholds)

    def _apply_hysteresis(self) -> None:
        self._skin.set_touch_hysteresis(self.hyst_spin.value())

    def _touch_settings_key(self) -> str:
        touch = getattr(self._skin, "touch", None) or {}
        return str(touch.get("node_mac") or getattr(self._skin, "skin_type", ""))

    def _saved_spike_threshold(self) -> float:
        from src.config.settings import Settings
        saved = Settings().touch_spike_threshold(self._touch_settings_key())
        if saved is not None:
            return saved
        thresholds = self._skin.touch_thresholds or [100.0]
        return max(20.0, float(thresholds[0]) * 0.25)

    def _apply_spike_threshold(self) -> None:
        value = self.spike_spin.value()
        touch = getattr(self._skin, "touch", None)
        if isinstance(touch, dict):
            touch["rhythm_spike_ut"] = value
        from src.config.settings import Settings
        Settings().set_touch_spike_threshold(self._touch_settings_key(), value)

    def _saved_frequency_reset_ms(self) -> float:
        from src.config.settings import Settings
        saved = Settings().touch_frequency_reset_ms(self._touch_settings_key())
        return 10000.0 if saved is None else saved

    def _apply_frequency_reset(self) -> None:
        value = self.frequency_reset_spin.value()
        self._frequency_reset_ms = value
        touch = getattr(self._skin, "touch", None)
        if isinstance(touch, dict):
            touch["frequency_reset_ms"] = value
        from src.config.settings import Settings
        Settings().set_touch_frequency_reset_ms(self._touch_settings_key(), value)

    def _saved_sync_tolerance_ms(self) -> float:
        from src.config.settings import Settings
        saved = Settings().touch_sync_tolerance_ms(self._touch_settings_key())
        return 150.0 if saved is None else saved

    def _apply_sync_tolerance(self) -> None:
        value = self.sync_tolerance_spin.value()
        touch = getattr(self._skin, "touch", None)
        if isinstance(touch, dict):
            touch["rhythm_sync_tolerance_ms"] = value
        from src.config.settings import Settings
        Settings().set_touch_sync_tolerance_ms(self._touch_settings_key(), value)

    def _rebaseline(self) -> None:
        sent = self._skin.rebaseline_touch()
        # Brief visual confirmation on the button.
        self.rebaseline_btn.setText("Re-zeroed" if sent else "Re-zeroed (local)")
        self.rebaseline_btn.setEnabled(False)
        QTimer.singleShot(900, self._restore_button)

    def _restore_button(self) -> None:
        self.rebaseline_btn.setText("Re-zero sensors")
        self.rebaseline_btn.setEnabled(True)

    def _apply_node_config(self) -> None:
        """Send configure to the firmware to set the node's activation threshold
        (the uT at/above which it reports a sensor in ``act``) to this skin
        type's saved sensitivity (calibrated in the guided gesture capture /
        Test Actuators), falling back to the firmware default."""
        ctrl = getattr(self._skin, "touch_controller", None)
        if ctrl is None or not hasattr(ctrl, "send_command"):
            return
        from src.config.settings import Settings
        saved = Settings().touch_threshold_ut(
            getattr(self._skin, "skin_type", "") or "")
        ctrl.send_command("configure", act_threshold_ut=saved or 300.0)

    def _apply_adaptive_baseline(self) -> None:
        """Toggle the node's adaptive baseline (and its time constant)."""
        ctrl = getattr(self._skin, "touch_controller", None)
        if ctrl is None or not hasattr(ctrl, "send_command"):
            return
        ctrl.send_command("configure",
                          adaptive_baseline=self.adaptive_chk.isChecked(),
                          baseline_tau_ms=self.tau_spin.value())
