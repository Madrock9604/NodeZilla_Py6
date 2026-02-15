from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QPointF, QSettings, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox,
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMdiArea,
    QPushButton,
    QSplitter,
    QDoubleSpinBox,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .discovery_backend import DiscoveryBackendAdapter, make_backend


@dataclass(frozen=True)
class InstrumentTool:
    """Static descriptor for one AD tool card in the Instruments tab."""

    key: str
    name: str
    description: str


class ToolPanel(QWidget):
    """Right-side content panel for one instrument tool."""

    def __init__(
        self,
        tool: InstrumentTool,
        on_start: Callable[[str], tuple[bool, str]],
        on_stop: Callable[[str], tuple[bool, str]],
        backend_name: str,
        connected_device: Callable[[], Optional[str]],
    ):
        super().__init__()
        self.tool = tool
        self._on_start = on_start
        self._on_stop = on_stop
        self._backend_name = backend_name
        self._connected_device = connected_device

        layout = QVBoxLayout(self)
        title = QLabel(tool.name)
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        desc = QLabel(tool.description)
        desc.setWordWrap(True)
        self.runtime = QLabel()
        self._update_runtime_line()

        action_row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.config_btn = QPushButton("Configure")
        action_row.addWidget(self.start_btn)
        action_row.addWidget(self.stop_btn)
        action_row.addWidget(self.config_btn)
        action_row.addStretch(1)

        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setPlaceholderText(
            "Session log."
        )

        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addWidget(self.runtime)
        layout.addLayout(action_row)
        layout.addWidget(self.notes, 1)

        self._set_running(False)
        self.start_btn.clicked.connect(self._start_requested)
        self.stop_btn.clicked.connect(self._stop_requested)
        self.config_btn.clicked.connect(
            lambda: self.notes.append("Configure UI is not implemented yet.")
        )

    def _start_requested(self):
        self._update_runtime_line()
        ok, msg = self._on_start(self.tool.key)
        self.notes.append(msg)
        if ok:
            self._set_running(True)

    def _stop_requested(self):
        self._update_runtime_line()
        ok, msg = self._on_stop(self.tool.key)
        self.notes.append(msg)
        if ok:
            self._set_running(False)

    def _update_runtime_line(self):
        dev = self._connected_device()
        if dev:
            self.runtime.setText(f"Runtime: {self._backend_name} | Device: {dev}")
        else:
            self.runtime.setText(f"Runtime: {self._backend_name} | Device: Disconnected")

    def on_connection_changed(self):
        self._update_runtime_line()

    def _set_running(self, running: bool):
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)


class ScopeWaveformWidget(QWidget):
    """Real-time waveform renderer with scope-style grid, ticks and axis labels."""

    def __init__(self):
        super().__init__()
        self._samples: Dict[str, List[float]] = {}
        self._autoscale = False
        self._sample_rate_hz = 1e5
        self._time_div_s = 1e-3
        self._ch1_vdiv = 0.5
        self._ch2_vdiv = 0.5
        self._ch1_offset_v = 0.0
        self._ch2_offset_v = 0.0
        self._ch2_enabled = False
        self.setMinimumHeight(280)

    def set_samples(self, samples: Dict[str, List[float]]):
        self._samples = {k: list(v) for k, v in samples.items()}
        self.update()

    def set_view(
        self,
        *,
        sample_rate_hz: float,
        time_div_s: float,
        ch1_vdiv: float,
        ch2_vdiv: float,
        ch1_offset_v: float,
        ch2_offset_v: float,
        ch2_enabled: bool,
        autoscale: bool,
    ):
        self._sample_rate_hz = max(1.0, float(sample_rate_hz))
        self._time_div_s = max(1e-6, float(time_div_s))
        self._ch1_vdiv = max(1e-3, float(ch1_vdiv))
        self._ch2_vdiv = max(1e-3, float(ch2_vdiv))
        self._ch1_offset_v = float(ch1_offset_v)
        self._ch2_offset_v = float(ch2_offset_v)
        self._ch2_enabled = bool(ch2_enabled)
        self._autoscale = bool(autoscale)
        self.update()

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        av = abs(seconds)
        if av >= 1.0:
            return f"{seconds:.2f} s"
        if av >= 1e-3:
            return f"{seconds * 1e3:.2f} ms"
        if av >= 1e-6:
            return f"{seconds * 1e6:.1f} us"
        return f"{seconds * 1e9:.1f} ns"

    @staticmethod
    def _fmt_volts(volts: float) -> str:
        av = abs(volts)
        if av >= 1.0:
            return f"{volts:.2f} V"
        if av >= 1e-3:
            return f"{volts * 1e3:.1f} mV"
        return f"{volts:.4f} V"

    def paintEvent(self, _event):
        p = QPainter(self)
        rect = self.rect()
        p.fillRect(rect, QColor(16, 16, 18))
        p.setRenderHint(QPainter.Antialiasing, True)

        # Keep margins for axis labels.
        left_pad = 62
        right_pad = 62
        top_pad = 12
        bottom_pad = 26
        plot = rect.adjusted(left_pad, top_pad, -right_pad, -bottom_pad)
        w = max(1, plot.width())
        h = max(1, plot.height())
        if w <= 2 or h <= 2:
            return

        # Scope-like grid (10 horizontal divisions, 8 vertical divisions).
        minor_pen = QPen(QColor(56, 56, 62), 1)
        major_pen = QPen(QColor(90, 90, 98), 1)
        center_pen = QPen(QColor(120, 120, 130), 1)
        for i in range(0, 11):
            x = int(plot.left() + i * w / 10)
            p.setPen(major_pen if i % 5 == 0 else minor_pen)
            p.drawLine(x, plot.top(), x, plot.bottom())
        for i in range(0, 9):
            y = int(plot.top() + i * h / 8)
            p.setPen(major_pen if i % 4 == 0 else minor_pen)
            p.drawLine(plot.left(), y, plot.right(), y)
        p.setPen(center_pen)
        p.drawLine(plot.left(), int(plot.top() + h / 2), plot.right(), int(plot.top() + h / 2))

        ch1 = self._samples.get("ch1", [])
        ch2 = self._samples.get("ch2", [])
        if not ch1 and not ch2:
            p.setPen(QPen(QColor(150, 150, 158), 1))
            p.drawText(plot.adjusted(8, 8, -8, -8), Qt.AlignTop | Qt.AlignLeft, "No capture")
            return

        ch1_vdiv_eff = self._ch1_vdiv
        ch2_vdiv_eff = self._ch2_vdiv
        ch1_off_eff = self._ch1_offset_v
        ch2_off_eff = self._ch2_offset_v
        if self._autoscale:
            if ch1:
                ch1_off_eff = sum(ch1) / max(1, len(ch1))
                span = max(ch1) - min(ch1) if len(ch1) > 1 else 1.0
                ch1_vdiv_eff = max(1e-3, span / 6.0)
            if ch2:
                ch2_off_eff = sum(ch2) / max(1, len(ch2))
                span = max(ch2) - min(ch2) if len(ch2) > 1 else 1.0
                ch2_vdiv_eff = max(1e-3, span / 6.0)

        def _draw_trace(samples: List[float], color: QColor, vdiv: float, offset_v: float):
            if not samples:
                return
            poly = QPolygonF()
            n = len(samples)
            px_per_v = (h / 8.0) / max(1e-6, vdiv)
            for i, v in enumerate(samples):
                x = float(plot.left()) if n <= 1 else float(plot.left() + (i / (n - 1)) * (w - 1))
                y = float(plot.top() + (h / 2.0) - ((v - offset_v) * px_per_v))
                poly.append(QPointF(x, y))
            p.setPen(QPen(color, 2))
            p.save()
            p.setClipRect(plot)
            p.drawPolyline(poly)
            p.restore()

        _draw_trace(ch1, QColor(255, 212, 84), ch1_vdiv_eff, ch1_off_eff)
        if self._ch2_enabled:
            _draw_trace(ch2, QColor(64, 220, 255), ch2_vdiv_eff, ch2_off_eff)

        # Axis labels and ticks.
        p.setPen(QPen(QColor(190, 190, 200), 1))
        for i in range(0, 11):
            x = int(plot.left() + i * w / 10)
            t = i * self._time_div_s
            p.drawText(x - 22, plot.bottom() + 16, 48, 14, Qt.AlignHCenter | Qt.AlignVCenter, self._fmt_time(t))
        for i in range(0, 9):
            y = int(plot.top() + i * h / 8)
            v1 = ch1_off_eff + (4 - i) * ch1_vdiv_eff
            p.drawText(2, y - 8, left_pad - 6, 16, Qt.AlignRight | Qt.AlignVCenter, self._fmt_volts(v1))
            if self._ch2_enabled:
                v2 = ch2_off_eff + (4 - i) * ch2_vdiv_eff
                p.drawText(plot.right() + 6, y - 8, right_pad - 8, 16, Qt.AlignLeft | Qt.AlignVCenter, self._fmt_volts(v2))

        # Border
        p.setPen(QPen(QColor(110, 110, 120), 1))
        p.drawRect(plot.adjusted(0, 0, -1, -1))
        p.setPen(QPen(QColor(150, 150, 160), 1))
        p.drawText(plot.left(), 2, plot.width(), top_pad - 2, Qt.AlignHCenter | Qt.AlignBottom, f"Time base: {self._fmt_time(self._time_div_s)}/div")


class WavegenPanel(QWidget):
    """Wavegen panel with dual-channel controls for AD2/AD3."""

    def __init__(self, tool: InstrumentTool, backend: DiscoveryBackendAdapter):
        super().__init__()
        self.tool = tool
        self.backend = backend
        self._running = False
        self._settings = QSettings("NodeZilla", "NodeZilla")
        self._profile_key = f"instruments/{tool.key}"

        layout = QVBoxLayout(self)
        title = QLabel(tool.name)
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        desc = QLabel(tool.description)
        desc.setWordWrap(True)
        self.runtime = QLabel()
        self.state_label = QLabel("State: Idle")

        actions = QHBoxLayout()
        self.start_btn = QPushButton("Run")
        self.stop_btn = QPushButton("Stop")
        self.apply_btn = QPushButton("Apply Config")
        actions.addWidget(self.start_btn)
        actions.addWidget(self.stop_btn)
        actions.addWidget(self.apply_btn)
        actions.addStretch(1)

        chan = QHBoxLayout()
        self.ch1_frame = QFrame()
        self.ch1_frame.setFrameShape(QFrame.StyledPanel)
        ch1_form = QFormLayout(self.ch1_frame)
        self.ch1_enable = QCheckBox("Enable CH1")
        self.ch1_enable.setChecked(True)
        self.ch1_waveform = QComboBox()
        self.ch1_waveform.addItems(["sine", "square", "triangle", "sawtooth", "dc"])
        self.ch1_freq = QDoubleSpinBox()
        self.ch1_freq.setRange(0.001, 20e6)
        self.ch1_freq.setDecimals(3)
        self.ch1_freq.setSingleStep(100.0)
        self.ch1_freq.setValue(1000.0)
        self.ch1_amp = QDoubleSpinBox()
        self.ch1_amp.setRange(0.0, 10.0)
        self.ch1_amp.setDecimals(3)
        self.ch1_amp.setSingleStep(0.1)
        self.ch1_amp.setValue(1.0)
        self.ch1_offset = QDoubleSpinBox()
        self.ch1_offset.setRange(-5.0, 5.0)
        self.ch1_offset.setDecimals(3)
        self.ch1_offset.setSingleStep(0.05)
        self.ch1_offset.setValue(0.0)
        ch1_form.addRow(self.ch1_enable)
        ch1_form.addRow("Waveform", self.ch1_waveform)
        ch1_form.addRow("Freq (Hz)", self.ch1_freq)
        ch1_form.addRow("Amplitude (V)", self.ch1_amp)
        ch1_form.addRow("Offset (V)", self.ch1_offset)

        self.ch2_frame = QFrame()
        self.ch2_frame.setFrameShape(QFrame.StyledPanel)
        ch2_form = QFormLayout(self.ch2_frame)
        self.ch2_enable = QCheckBox("Enable CH2")
        self.ch2_enable.setChecked(False)
        self.ch2_waveform = QComboBox()
        self.ch2_waveform.addItems(["sine", "square", "triangle", "sawtooth", "dc"])
        self.ch2_freq = QDoubleSpinBox()
        self.ch2_freq.setRange(0.001, 20e6)
        self.ch2_freq.setDecimals(3)
        self.ch2_freq.setSingleStep(100.0)
        self.ch2_freq.setValue(1000.0)
        self.ch2_amp = QDoubleSpinBox()
        self.ch2_amp.setRange(0.0, 10.0)
        self.ch2_amp.setDecimals(3)
        self.ch2_amp.setSingleStep(0.1)
        self.ch2_amp.setValue(1.0)
        self.ch2_offset = QDoubleSpinBox()
        self.ch2_offset.setRange(-5.0, 5.0)
        self.ch2_offset.setDecimals(3)
        self.ch2_offset.setSingleStep(0.05)
        self.ch2_offset.setValue(0.0)
        ch2_form.addRow(self.ch2_enable)
        ch2_form.addRow("Waveform", self.ch2_waveform)
        ch2_form.addRow("Freq (Hz)", self.ch2_freq)
        ch2_form.addRow("Amplitude (V)", self.ch2_amp)
        ch2_form.addRow("Offset (V)", self.ch2_offset)

        chan.addWidget(self.ch1_frame, 1)
        chan.addWidget(self.ch2_frame, 1)

        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMinimumHeight(140)

        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addWidget(self.runtime)
        layout.addWidget(self.state_label)
        layout.addLayout(actions)
        layout.addLayout(chan)
        layout.addWidget(self.notes, 1)

        self._reconfig_timer = QTimer(self)
        self._reconfig_timer.setSingleShot(True)
        self._reconfig_timer.setInterval(300)
        self._reconfig_timer.timeout.connect(self._run_live_reconfigure)

        self.start_btn.clicked.connect(self._start_requested)
        self.stop_btn.clicked.connect(self._stop_requested)
        self.apply_btn.clicked.connect(self._apply_config)

        self.ch1_enable.toggled.connect(self._save_profile)
        self.ch1_waveform.currentTextChanged.connect(self._save_profile)
        self.ch1_freq.valueChanged.connect(self._save_profile)
        self.ch1_amp.valueChanged.connect(self._save_profile)
        self.ch1_offset.valueChanged.connect(self._save_profile)
        self.ch2_enable.toggled.connect(self._save_profile)
        self.ch2_waveform.currentTextChanged.connect(self._save_profile)
        self.ch2_freq.valueChanged.connect(self._save_profile)
        self.ch2_amp.valueChanged.connect(self._save_profile)
        self.ch2_offset.valueChanged.connect(self._save_profile)

        self.ch1_enable.toggled.connect(self._schedule_live_reconfigure)
        self.ch1_waveform.currentTextChanged.connect(self._schedule_live_reconfigure)
        self.ch1_freq.valueChanged.connect(self._schedule_live_reconfigure)
        self.ch1_amp.valueChanged.connect(self._schedule_live_reconfigure)
        self.ch1_offset.valueChanged.connect(self._schedule_live_reconfigure)
        self.ch2_enable.toggled.connect(self._schedule_live_reconfigure)
        self.ch2_waveform.currentTextChanged.connect(self._schedule_live_reconfigure)
        self.ch2_freq.valueChanged.connect(self._schedule_live_reconfigure)
        self.ch2_amp.valueChanged.connect(self._schedule_live_reconfigure)
        self.ch2_offset.valueChanged.connect(self._schedule_live_reconfigure)

        self._load_profile()
        self._set_running(False)
        self._update_runtime_line()

    def _params(self) -> dict:
        return {
            "ch1_enabled": bool(self.ch1_enable.isChecked()),
            "ch1_waveform": self.ch1_waveform.currentText(),
            "ch1_frequency_hz": float(self.ch1_freq.value()),
            "ch1_amplitude_v": float(self.ch1_amp.value()),
            "ch1_offset_v": float(self.ch1_offset.value()),
            "ch2_enabled": bool(self.ch2_enable.isChecked()),
            "ch2_waveform": self.ch2_waveform.currentText(),
            "ch2_frequency_hz": float(self.ch2_freq.value()),
            "ch2_amplitude_v": float(self.ch2_amp.value()),
            "ch2_offset_v": float(self.ch2_offset.value()),
        }

    def _apply_config(self, quiet: bool = False):
        ok, msg = self.backend.configure_wavegen(**self._params())
        if not quiet or not ok:
            self.notes.append(msg)
        return ok

    def _start_requested(self):
        self._update_runtime_line()
        if not self._apply_config():
            self._set_running(False)
            return
        ok, msg = self.backend.start_tool("wavegen")
        self.notes.append(msg)
        if ok:
            self._set_running(True)
            self.state_label.setText("State: Running")

    def _stop_requested(self):
        self._reconfig_timer.stop()
        ok, msg = self.backend.stop_tool("wavegen")
        self.notes.append(msg)
        self._set_running(False)
        self.state_label.setText("State: Stopped")
        return ok

    def _schedule_live_reconfigure(self, *_args):
        if self._running:
            self._reconfig_timer.start()

    def _run_live_reconfigure(self):
        if not self._running:
            return
        if not self._apply_config(quiet=True):
            self.state_label.setText("State: Error")
            return
        ok, msg = self.backend.start_tool("wavegen")
        if not ok:
            self.notes.append(msg)
            self._set_running(False)
            self.state_label.setText("State: Error")

    def _set_running(self, running: bool):
        self._running = running
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    def _update_runtime_line(self):
        dev = self.backend.connected_device()
        if dev:
            self.runtime.setText(f"Runtime: {self.backend.backend_name()} | Device: {dev}")
        else:
            self.runtime.setText(f"Runtime: {self.backend.backend_name()} | Device: Disconnected")

    def on_connection_changed(self):
        self._update_runtime_line()
        if self.backend.connected_device() is None and self._running:
            self._set_running(False)
            self.state_label.setText("State: Disconnected")

    def _save_profile(self, *_args):
        base = self._profile_key
        self._settings.setValue(f"{base}/ch1_enable", bool(self.ch1_enable.isChecked()))
        self._settings.setValue(f"{base}/ch1_waveform", self.ch1_waveform.currentText())
        self._settings.setValue(f"{base}/ch1_freq", float(self.ch1_freq.value()))
        self._settings.setValue(f"{base}/ch1_amp", float(self.ch1_amp.value()))
        self._settings.setValue(f"{base}/ch1_offset", float(self.ch1_offset.value()))
        self._settings.setValue(f"{base}/ch2_enable", bool(self.ch2_enable.isChecked()))
        self._settings.setValue(f"{base}/ch2_waveform", self.ch2_waveform.currentText())
        self._settings.setValue(f"{base}/ch2_freq", float(self.ch2_freq.value()))
        self._settings.setValue(f"{base}/ch2_amp", float(self.ch2_amp.value()))
        self._settings.setValue(f"{base}/ch2_offset", float(self.ch2_offset.value()))

    def _load_profile(self):
        base = self._profile_key
        ch1_enable = str(self._settings.value(f"{base}/ch1_enable", "true")).lower() in ("1", "true", "yes")
        ch1_wave = str(self._settings.value(f"{base}/ch1_waveform", "sine"))
        ch1_freq = float(self._settings.value(f"{base}/ch1_freq", 1000.0))
        ch1_amp = float(self._settings.value(f"{base}/ch1_amp", 1.0))
        ch1_off = float(self._settings.value(f"{base}/ch1_offset", 0.0))
        ch2_enable = str(self._settings.value(f"{base}/ch2_enable", "false")).lower() in ("1", "true", "yes")
        ch2_wave = str(self._settings.value(f"{base}/ch2_waveform", "sine"))
        ch2_freq = float(self._settings.value(f"{base}/ch2_freq", 1000.0))
        ch2_amp = float(self._settings.value(f"{base}/ch2_amp", 1.0))
        ch2_off = float(self._settings.value(f"{base}/ch2_offset", 0.0))

        self.ch1_enable.setChecked(ch1_enable)
        if self.ch1_waveform.findText(ch1_wave) >= 0:
            self.ch1_waveform.setCurrentText(ch1_wave)
        self.ch1_freq.setValue(ch1_freq)
        self.ch1_amp.setValue(ch1_amp)
        self.ch1_offset.setValue(ch1_off)

        self.ch2_enable.setChecked(ch2_enable)
        if self.ch2_waveform.findText(ch2_wave) >= 0:
            self.ch2_waveform.setCurrentText(ch2_wave)
        self.ch2_freq.setValue(ch2_freq)
        self.ch2_amp.setValue(ch2_amp)
        self.ch2_offset.setValue(ch2_off)


class ScopePanel(QWidget):
    """Scope-specific panel with real backend config, polling, and measurements."""

    def __init__(
        self,
        tool: InstrumentTool,
        backend: DiscoveryBackendAdapter,
    ):
        super().__init__()
        self.tool = tool
        self.backend = backend
        self._running = False
        self._last_samples: List[float] = []
        self._last_ch2_samples: List[float] = []
        self._last_trigger_idx: Optional[int] = None
        self._display_buffer_size = 1024
        self._display_ch1: List[float] = []
        self._display_ch2: List[float] = []
        self._record_ch1: List[float] = []
        self._record_ch2: List[float] = []
        self._screen_write_idx = 0
        self._settings = QSettings("NodeZilla", "NodeZilla")
        self._profile_key = f"instruments/{tool.key}"

        layout = QVBoxLayout(self)
        self.runtime = QLabel()
        self.state_label = QLabel("State: Idle")

        top = QHBoxLayout()
        self.start_btn = QPushButton("Run")
        self.stop_btn = QPushButton("Stop")
        self.single_btn = QPushButton("Single")
        self.config_btn = QPushButton("Apply")
        self.buffer_count = QSpinBox()
        self.buffer_count.setRange(1, 64)
        self.buffer_count.setValue(10)
        self.buffer_count.setToolTip("Script: Scope1.Buffer.value/text")
        self.update_mode = QComboBox()
        self.update_mode.addItems(["repeated", "shift", "screen", "record"])
        self.sample_rate = QComboBox()
        self.sample_rate.addItems(["1e3", "1e4", "5e4", "1e5", "5e5", "1e6"])
        self.sample_rate.setCurrentText("1e5")
        self.time_div = QComboBox()
        self.time_div.addItems([
            "50 us/div",
            "100 us/div",
            "200 us/div",
            "500 us/div",
            "1 ms/div",
            "2 ms/div",
            "5 ms/div",
            "10 ms/div",
            "20 ms/div",
            "50 ms/div",
            "100 ms/div",
        ])
        self.time_div.setCurrentText("1 ms/div")
        self.trigger_mode = QComboBox()
        self.trigger_mode.addItems(["auto", "normal"])
        self.trigger_source = QComboBox()
        self.trigger_source.addItems(["ch1", "ch2"])
        self.trigger_edge = QComboBox()
        self.trigger_edge.addItems(["rising", "falling"])
        self.trigger_level = QDoubleSpinBox()
        self.trigger_level.setRange(-20.0, 20.0)
        self.trigger_level.setDecimals(3)
        self.trigger_level.setSingleStep(0.05)
        self.trigger_level.setValue(0.0)

        top.addWidget(self.start_btn)
        top.addWidget(self.stop_btn)
        top.addWidget(self.single_btn)
        top.addWidget(QLabel("Buffer"))
        top.addWidget(self.buffer_count)
        top.addWidget(QLabel("Mode"))
        top.addWidget(self.update_mode)
        top.addWidget(QLabel("Fs"))
        top.addWidget(self.sample_rate)
        top.addWidget(QLabel("Time"))
        top.addWidget(self.time_div)
        top.addWidget(QLabel("Trigger"))
        top.addWidget(self.trigger_mode)
        top.addWidget(QLabel("Src"))
        top.addWidget(self.trigger_source)
        top.addWidget(self.trigger_edge)
        top.addWidget(QLabel("Lvl"))
        top.addWidget(self.trigger_level)
        top.addWidget(self.config_btn)
        top.addStretch(1)

        meas = QHBoxLayout()
        self.m_vpp = QLabel("Vpp: --")
        self.m_vrms = QLabel("Vrms: --")
        self.m_vmean = QLabel("Vmean: --")
        self.m2_vpp = QLabel("CH2 Vpp: --")
        self.m2_vrms = QLabel("CH2 Vrms: --")
        self.m_points = QLabel("Samples: 0")
        self.span_label = QLabel("Span: --")
        for w in (self.runtime, self.state_label, self.span_label, self.m_vpp, self.m_vrms, self.m_vmean, self.m2_vpp, self.m2_vrms, self.m_points):
            meas.addWidget(w)
        meas.addStretch(1)

        center = QHBoxLayout()
        self.wave = ScopeWaveformWidget()
        self.wave.setMinimumWidth(760)
        center.addWidget(self.wave, 1)

        side = QVBoxLayout()
        time_frame = QFrame()
        time_frame.setFrameShape(QFrame.StyledPanel)
        time_form = QFormLayout(time_frame)
        self.time_pos = QDoubleSpinBox()
        self.time_pos.setRange(-10.0, 10.0)
        self.time_pos.setDecimals(6)
        self.time_pos.setSingleStep(0.001)
        self.time_pos.setSuffix(" s")
        self.time_pos.setValue(0.0)
        time_form.addRow("Time Position", self.time_pos)
        time_form.addRow("Time Base", QLabel("Use top toolbar"))
        time_form.addRow("Sample Rate", QLabel("Use top toolbar"))

        options_frame = QFrame()
        options_frame.setFrameShape(QFrame.StyledPanel)
        options_form = QFormLayout(options_frame)
        self.autoscale_chk = QCheckBox("Autoscale")
        self.autoscale_chk.setChecked(False)
        options_form.addRow(self.autoscale_chk)
        options_form.addRow("Update Mode", QLabel("Use top toolbar"))
        options_form.addRow("Trigger Src", QLabel("Use top toolbar"))

        self.ch1_frame = QFrame()
        self.ch1_frame.setFrameShape(QFrame.StyledPanel)
        ch1_form = QFormLayout(self.ch1_frame)
        self.ch1_vdiv = QComboBox()
        self.ch1_vdiv.addItems(["50 mV/div", "100 mV/div", "200 mV/div", "500 mV/div", "1 V/div", "2 V/div", "5 V/div"])
        self.ch1_vdiv.setCurrentText("500 mV/div")
        self.ch1_offset = QDoubleSpinBox()
        self.ch1_offset.setRange(-20.0, 20.0)
        self.ch1_offset.setDecimals(3)
        self.ch1_offset.setSingleStep(0.05)
        self.ch1_offset.setValue(0.0)
        ch1_form.addRow(QLabel("Channel 1"))
        ch1_form.addRow("Scale", self.ch1_vdiv)
        ch1_form.addRow("Offset (V)", self.ch1_offset)

        self.ch2_frame = QFrame()
        self.ch2_frame.setFrameShape(QFrame.StyledPanel)
        ch2_form = QFormLayout(self.ch2_frame)
        self.ch2_enable = QCheckBox("Enable CH2")
        self.ch2_enable.setChecked(False)
        self.ch2_vdiv = QComboBox()
        self.ch2_vdiv.addItems(["50 mV/div", "100 mV/div", "200 mV/div", "500 mV/div", "1 V/div", "2 V/div", "5 V/div"])
        self.ch2_vdiv.setCurrentText("500 mV/div")
        self.ch2_offset = QDoubleSpinBox()
        self.ch2_offset.setRange(-20.0, 20.0)
        self.ch2_offset.setDecimals(3)
        self.ch2_offset.setSingleStep(0.05)
        self.ch2_offset.setValue(0.0)
        ch2_form.addRow(self.ch2_enable)
        ch2_form.addRow("Scale", self.ch2_vdiv)
        ch2_form.addRow("Offset (V)", self.ch2_offset)

        side.addWidget(time_frame)
        side.addWidget(options_frame)
        side.addWidget(self.ch1_frame)
        side.addWidget(self.ch2_frame)
        side.addStretch(1)
        center.addLayout(side)

        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(90)

        layout.addLayout(top)
        layout.addLayout(meas)
        layout.addLayout(center, 1)
        layout.addWidget(self.notes)

        self.timer = QTimer(self)
        self.timer.setInterval(60)
        self.timer.timeout.connect(self._poll_scope)
        self._reconfig_timer = QTimer(self)
        self._reconfig_timer.setSingleShot(True)
        self._reconfig_timer.setInterval(300)
        self._reconfig_timer.timeout.connect(self._run_live_reconfigure)

        self.start_btn.clicked.connect(self._start_requested)
        self.stop_btn.clicked.connect(self._stop_requested)
        self.single_btn.clicked.connect(self._single_requested)
        self.config_btn.clicked.connect(self._apply_config)
        self.sample_rate.currentTextChanged.connect(self._update_span_label)
        self.time_div.currentTextChanged.connect(self._update_span_label)
        self.autoscale_chk.toggled.connect(self._sync_wave_view)
        self.ch1_vdiv.currentTextChanged.connect(self._sync_wave_view)
        self.ch2_vdiv.currentTextChanged.connect(self._sync_wave_view)
        self.ch1_offset.valueChanged.connect(self._sync_wave_view)
        self.ch2_offset.valueChanged.connect(self._sync_wave_view)
        self.time_div.currentTextChanged.connect(self._sync_wave_view)
        self.sample_rate.currentTextChanged.connect(self._sync_wave_view)
        self.ch2_enable.toggled.connect(self._sync_wave_view)
        self.sample_rate.currentTextChanged.connect(self._save_profile)
        self.buffer_count.valueChanged.connect(self._save_profile)
        self.time_div.currentTextChanged.connect(self._save_profile)
        self.ch1_vdiv.currentTextChanged.connect(self._save_profile)
        self.ch2_vdiv.currentTextChanged.connect(self._save_profile)
        self.ch1_offset.valueChanged.connect(self._save_profile)
        self.ch2_offset.valueChanged.connect(self._save_profile)
        self.autoscale_chk.toggled.connect(self._save_profile)
        self.update_mode.currentTextChanged.connect(self._save_profile)
        self.update_mode.currentTextChanged.connect(self._on_update_mode_changed)
        self.trigger_mode.currentTextChanged.connect(self._save_profile)
        self.trigger_source.currentTextChanged.connect(self._save_profile)
        self.trigger_edge.currentTextChanged.connect(self._save_profile)
        self.trigger_level.valueChanged.connect(self._save_profile)
        self.ch2_enable.toggled.connect(self._save_profile)
        self.ch2_enable.toggled.connect(self._sync_ch2_ui)
        self.sample_rate.currentTextChanged.connect(self._schedule_live_reconfigure)
        self.buffer_count.valueChanged.connect(self._schedule_live_reconfigure)
        self.time_div.currentTextChanged.connect(self._schedule_live_reconfigure)
        self.ch1_vdiv.currentTextChanged.connect(self._schedule_live_reconfigure)
        self.ch2_vdiv.currentTextChanged.connect(self._schedule_live_reconfigure)
        self.ch1_offset.valueChanged.connect(self._schedule_live_reconfigure)
        self.ch2_offset.valueChanged.connect(self._schedule_live_reconfigure)
        self.trigger_mode.currentTextChanged.connect(self._schedule_live_reconfigure)
        self.trigger_source.currentTextChanged.connect(self._schedule_live_reconfigure)
        self.trigger_edge.currentTextChanged.connect(self._schedule_live_reconfigure)
        self.trigger_level.valueChanged.connect(self._schedule_live_reconfigure)
        self.ch2_enable.toggled.connect(self._schedule_live_reconfigure)
        self._load_profile()
        self._set_running(False)
        self._update_span_label()
        self._update_runtime_line()
        self._sync_ch2_ui()
        self._sync_wave_view()

    @staticmethod
    def _parse_time_div(text: str) -> float:
        val = text.strip().lower()
        if "us/div" in val:
            return float(val.split("us/div")[0].strip()) * 1e-6
        if "ms/div" in val:
            return float(val.split("ms/div")[0].strip()) * 1e-3
        if "s/div" in val:
            return float(val.split("s/div")[0].strip())
        return 1e-3

    @staticmethod
    def _parse_vdiv(text: str) -> float:
        val = text.strip().lower()
        if "mv/div" in val:
            return float(val.split("mv/div")[0].strip()) * 1e-3
        if "v/div" in val:
            return float(val.split("v/div")[0].strip())
        return 0.5

    def _capture_buffer_size(self, fs: float, time_div_s: float) -> int:
        # 10 horizontal divisions.
        buf = int(max(64, min(8192, round(fs * time_div_s * 10.0))))
        # Keep a practical granularity.
        if buf < 64:
            return 64
        return int(max(64, min(8192, int(math.ceil(buf / 32.0) * 32))))

    def _scope_params(self) -> tuple[float, int, int, float, float, float, float, float]:
        fs = float(self.sample_rate.currentText())
        time_div_s = self._parse_time_div(self.time_div.currentText())
        disp_buf = self._capture_buffer_size(fs, time_div_s)
        cap_mul = max(1, int(self.buffer_count.value()))
        cap_buf = int(max(64, min(8192, disp_buf * cap_mul)))
        ch1_vdiv = self._parse_vdiv(self.ch1_vdiv.currentText())
        ch2_vdiv = self._parse_vdiv(self.ch2_vdiv.currentText())
        # Backend expects channel full range; map from volts/div (8 vertical divisions).
        ch1_range = max(0.1, ch1_vdiv * 8.0)
        ch2_range = max(0.1, ch2_vdiv * 8.0)
        ch1_offset = float(self.ch1_offset.value())
        ch2_offset = float(self.ch2_offset.value())
        return fs, disp_buf, cap_buf, ch1_range, ch2_range, ch1_offset, ch2_offset, time_div_s

    def _apply_config(self, quiet: bool = False):
        fs, disp_buf, cap_buf, ch1_range, ch2_range, ch1_offset, ch2_offset, _ = self._scope_params()
        self._display_buffer_size = int(disp_buf)
        ok, msg = self.backend.configure_scope(
            fs,
            cap_buf,
            ch1_range,
            trigger_mode=self.trigger_mode.currentText(),
            trigger_edge=self.trigger_edge.currentText(),
            trigger_level_v=float(self.trigger_level.value()),
            ch2_enabled=bool(self.ch2_enable.isChecked()),
            ch2_range_v=ch2_range,
            ch1_offset_v=ch1_offset,
            ch2_offset_v=ch2_offset,
        )
        if not quiet or not ok:
            self.notes.append(msg)
        return ok

    def _single_requested(self):
        self._update_runtime_line()
        if not self._apply_config():
            self._set_running(False)
            return
        ok, msg = self.backend.start_tool("scope")
        self.notes.append(msg)
        if not ok:
            return
        self._poll_scope()
        self.backend.stop_tool("scope")
        self._set_running(False)
        self.state_label.setText("State: Single capture complete")

    def _start_requested(self):
        self._update_runtime_line()
        self._reset_display_state()
        self._last_trigger_idx = None
        if not self._apply_config():
            self._set_running(False)
            return
        ok, msg = self.backend.start_tool("scope")
        self.notes.append(msg)
        if ok:
            self.timer.start()
            self._set_running(True)
            self.state_label.setText("State: Running")

    def _stop_requested(self):
        self._update_runtime_line()
        self.timer.stop()
        self._reconfig_timer.stop()
        self._last_trigger_idx = None
        ok, msg = self.backend.stop_tool("scope")
        self.notes.append(msg)
        self._set_running(False)
        self.state_label.setText("State: Stopped")
        return ok

    def _poll_scope(self):
        fs, disp_buf, cap_buf, _, _, _, _, _ = self._scope_params()
        max_samples = int(cap_buf)
        ok, msg, channels = self.backend.read_scope_channels(max_samples)
        if not ok:
            self.notes.append(msg)
            self.timer.stop()
            self._set_running(False)
            self.state_label.setText("State: Error")
            return
        ch1 = channels.get("ch1", [])
        ch2 = channels.get("ch2", [])
        if ch1 or ch2:
            d1, d2 = self._apply_update_mode(ch1, ch2, int(disp_buf))
            self._last_samples = list(d1)
            self._last_ch2_samples = list(d2)
            self.wave.set_samples({"ch1": d1, "ch2": d2})
            self._update_measurements(d1, d2)
            self.m_points.setText(f"Samples: {len(d1)} @ {fs:.0f} Hz")

    def _set_running(self, running: bool):
        self._running = running
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.config_btn.setEnabled(not running)
        self.single_btn.setEnabled(not running)

    def _update_runtime_line(self):
        dev = self.backend.connected_device()
        if dev:
            self.runtime.setText(f"Runtime: {self.backend.backend_name()} | Device: {dev}")
        else:
            self.runtime.setText(
                f"Runtime: {self.backend.backend_name()} | Device: Disconnected"
            )

    def on_connection_changed(self):
        self._update_runtime_line()
        if self.backend.connected_device() is None and self._running:
            self.timer.stop()
            self._set_running(False)
            self.state_label.setText("State: Disconnected")

    def _update_span_label(self):
        fs, _, _, _, _, _, _, time_div_s = self._scope_params()
        span_s = 10.0 * time_div_s
        self.span_label.setText(f"Span: {span_s * 1e3:.2f} ms")

    def _sync_wave_view(self):
        fs, _, _, _, _, ch1_offset, ch2_offset, time_div_s = self._scope_params()
        self.wave.set_view(
            sample_rate_hz=fs,
            time_div_s=time_div_s,
            ch1_vdiv=self._parse_vdiv(self.ch1_vdiv.currentText()),
            ch2_vdiv=self._parse_vdiv(self.ch2_vdiv.currentText()),
            ch1_offset_v=ch1_offset,
            ch2_offset_v=ch2_offset,
            ch2_enabled=bool(self.ch2_enable.isChecked()),
            autoscale=self.autoscale_chk.isChecked(),
        )

    def _sync_ch2_ui(self):
        enabled = bool(self.ch2_enable.isChecked())
        self.ch2_frame.setVisible(True)
        self.ch2_vdiv.setEnabled(enabled)
        self.ch2_offset.setEnabled(enabled)
        self.m2_vpp.setVisible(enabled)
        self.m2_vrms.setVisible(enabled)
        if not enabled:
            self._last_ch2_samples = []
            self.wave.set_samples({"ch1": self._last_samples, "ch2": []})
            self.m2_vpp.setText("CH2 Vpp: --")
            self.m2_vrms.setText("CH2 Vrms: --")

    def _reset_display_state(self):
        self._display_ch1 = []
        self._display_ch2 = []
        self._record_ch1 = []
        self._record_ch2 = []
        self._screen_write_idx = 0

    def _on_update_mode_changed(self, _mode: str):
        self._reset_display_state()
        self._last_trigger_idx = None

    def _ensure_display_len(self, data: List[float], size: int) -> List[float]:
        if not data:
            return [0.0] * size
        if len(data) >= size:
            return list(data[-size:])
        return list(data) + [data[-1]] * (size - len(data))

    def _shift_non_wrapped(self, data: List[float], shift: int) -> List[float]:
        if not data or shift == 0:
            return list(data)
        n = len(data)
        s = shift % n
        if s == 0:
            return list(data)
        return data[-s:] + data[:-s]

    def _extract_trigger_window(self, ch1: List[float], ch2: List[float], display_size: int) -> tuple[List[float], List[float]]:
        if not ch1:
            return [], []
        src = self.trigger_source.currentText()
        sig = ch2 if (src == "ch2" and ch2) else ch1
        n = len(sig)
        display_size = max(64, min(display_size, n))
        level = float(self.trigger_level.value())
        rising = self.trigger_edge.currentText() == "rising"
        target = max(1, display_size // 10)
        candidates: List[int] = []
        for i in range(1, n):
            a = sig[i - 1]
            b = sig[i]
            crossed = (a < level <= b) if rising else (a > level >= b)
            if crossed and i >= target and (i - target + display_size) <= n:
                candidates.append(i)
        if not candidates:
            self._last_trigger_idx = None
            start = max(0, n - display_size)
            out1 = ch1[start:start + display_size]
            out2 = ch2[start:start + display_size] if ch2 else []
            return self._ensure_display_len(out1, display_size), self._ensure_display_len(out2, display_size) if out2 else []
        idx = min(candidates, key=lambda j: abs(j - target))
        if self._last_trigger_idx is not None:
            lock = max(2, display_size // 20)
            near = [j for j in candidates if abs(j - self._last_trigger_idx) <= lock]
            if near:
                idx = min(near, key=lambda j: abs(j - self._last_trigger_idx))
        self._last_trigger_idx = idx
        start = idx - target
        end = start + display_size
        out1 = ch1[start:end]
        out2 = ch2[start:end] if ch2 else []
        return out1, out2

    def _downsample_to(self, data: List[float], size: int) -> List[float]:
        if not data:
            return [0.0] * size
        if len(data) <= size:
            return self._ensure_display_len(data, size)
        out: List[float] = []
        n = len(data)
        for i in range(size):
            idx = int(i * (n - 1) / max(1, size - 1))
            out.append(data[idx])
        return out

    def _apply_update_mode(self, ch1: List[float], ch2: List[float], display_size: int) -> tuple[List[float], List[float]]:
        mode = self.update_mode.currentText()

        if mode == "repeated":
            a1, a2 = self._extract_trigger_window(ch1, ch2, display_size)
            self._display_ch1 = list(a1)
            self._display_ch2 = list(a2)
            return a1, a2

        if mode == "shift":
            step = max(1, min(display_size // 8, len(ch1)))
            if not self._display_ch1:
                self._display_ch1 = self._ensure_display_len(ch1, display_size)
                self._display_ch2 = self._ensure_display_len(ch2, display_size) if ch2 else [0.0] * display_size
            else:
                self._display_ch1 = self._display_ch1[step:] + list(ch1[-step:])
                if ch2:
                    self._display_ch2 = self._display_ch2[step:] + list(ch2[-step:])
                else:
                    self._display_ch2 = self._display_ch2[step:] + [0.0] * step
            return self._display_ch1, self._display_ch2 if self.ch2_enable.isChecked() else []

        if mode == "screen":
            if not self._display_ch1:
                self._display_ch1 = [0.0] * display_size
                self._display_ch2 = [0.0] * display_size
                self._screen_write_idx = 0
            step = max(1, min(display_size // 16, len(ch1)))
            new1 = list(ch1[-step:])
            new2 = list(ch2[-step:]) if ch2 else [0.0] * step
            for i in range(step):
                idx = (self._screen_write_idx + i) % display_size
                self._display_ch1[idx] = new1[i]
                self._display_ch2[idx] = new2[i]
            self._screen_write_idx = (self._screen_write_idx + step) % display_size
            return self._display_ch1, self._display_ch2 if self.ch2_enable.isChecked() else []

        # record
        self._record_ch1.extend(ch1)
        if ch2:
            self._record_ch2.extend(ch2)
        max_hist = display_size * 32
        if len(self._record_ch1) > max_hist:
            self._record_ch1 = self._record_ch1[-max_hist:]
        if len(self._record_ch2) > max_hist:
            self._record_ch2 = self._record_ch2[-max_hist:]
        d1 = self._downsample_to(self._record_ch1, display_size)
        d2 = self._downsample_to(self._record_ch2, display_size) if self.ch2_enable.isChecked() else []
        return d1, d2

    def _align_to_trigger(self, ch1: List[float], ch2: List[float]) -> tuple[List[float], List[float]]:
        if len(ch1) < 4:
            return ch1, ch2
        level = float(self.trigger_level.value())
        rising = self.trigger_edge.currentText() == "rising"
        candidates: List[int] = []
        for i in range(1, len(ch1)):
            a = ch1[i - 1]
            b = ch1[i]
            crossed = (a < level <= b) if rising else (a > level >= b)
            if crossed:
                candidates.append(i)
        if not candidates:
            self._last_trigger_idx = None
            return ch1, ch2
        target = max(1, len(ch1) // 10)
        # Estimate period from crossings so we can stabilize pick across frames.
        period = 0
        if len(candidates) > 1:
            diffs = [candidates[i] - candidates[i - 1] for i in range(1, len(candidates))]
            diffs = [d for d in diffs if d > 0]
            if diffs:
                diffs.sort()
                period = diffs[len(diffs) // 2]

        idx = min(candidates, key=lambda j: abs(j - target))
        if self._last_trigger_idx is not None:
            if period > 0:
                lock_window = max(2, period // 3)
            else:
                lock_window = max(2, len(ch1) // 40)
            stable = [j for j in candidates if abs(j - self._last_trigger_idx) <= lock_window]
            if stable:
                idx = min(stable, key=lambda j: abs(j - self._last_trigger_idx))
        self._last_trigger_idx = idx
        shift = target - idx
        # Keep trigger correction bounded to avoid large edge padding artifacts.
        max_shift = max(2, len(ch1) // 8)
        if abs(shift) > max_shift:
            shift = 0
        return self._shift_non_wrapped(ch1, shift), self._shift_non_wrapped(ch2, shift)

    def _schedule_live_reconfigure(self, *_args):
        if self._running:
            self._reconfig_timer.start()

    def _run_live_reconfigure(self):
        if not self._running:
            return
        if not self._apply_config(quiet=True):
            return
        # Re-arm acquisition after live reconfigure because some backends
        # reset streaming state while applying channel settings.
        ok, msg = self.backend.start_tool("scope")
        if not ok:
            self.notes.append(msg)
            self.timer.stop()
            self._set_running(False)
            self.state_label.setText("State: Error")

    def _update_measurements(self, ch1: List[float], ch2: List[float]):
        if not ch1:
            return
        n = len(ch1)
        vmin = min(ch1)
        vmax = max(ch1)
        vpp = vmax - vmin
        mean = sum(ch1) / n
        vrms = (sum(v * v for v in ch1) / n) ** 0.5
        self.m_vpp.setText(f"Vpp: {vpp:.3f} V")
        self.m_vmean.setText(f"Vmean: {mean:.3f} V")
        self.m_vrms.setText(f"Vrms: {vrms:.3f} V")
        self.m_points.setText(f"Samples: {n}")
        if ch2:
            n2 = len(ch2)
            vpp2 = max(ch2) - min(ch2)
            vrms2 = (sum(v * v for v in ch2) / n2) ** 0.5
            self.m2_vpp.setText(f"CH2 Vpp: {vpp2:.3f} V")
            self.m2_vrms.setText(f"CH2 Vrms: {vrms2:.3f} V")
        else:
            self.m2_vpp.setText("CH2 Vpp: --")
            self.m2_vrms.setText("CH2 Vrms: --")

    def _save_profile(self, *_args):
        base = self._profile_key
        self._settings.setValue(f"{base}/sample_rate", self.sample_rate.currentText())
        self._settings.setValue(f"{base}/buffer_count", int(self.buffer_count.value()))
        self._settings.setValue(f"{base}/time_div", self.time_div.currentText())
        self._settings.setValue(f"{base}/ch1_vdiv", self.ch1_vdiv.currentText())
        self._settings.setValue(f"{base}/ch2_vdiv", self.ch2_vdiv.currentText())
        self._settings.setValue(f"{base}/ch1_offset_v", float(self.ch1_offset.value()))
        self._settings.setValue(f"{base}/ch2_offset_v", float(self.ch2_offset.value()))
        self._settings.setValue(f"{base}/autoscale", bool(self.autoscale_chk.isChecked()))
        self._settings.setValue(f"{base}/update_mode", self.update_mode.currentText())
        self._settings.setValue(f"{base}/trigger_mode", self.trigger_mode.currentText())
        self._settings.setValue(f"{base}/trigger_source", self.trigger_source.currentText())
        self._settings.setValue(f"{base}/trigger_edge", self.trigger_edge.currentText())
        self._settings.setValue(f"{base}/trigger_level", float(self.trigger_level.value()))
        self._settings.setValue(f"{base}/ch2_enable", bool(self.ch2_enable.isChecked()))

    def _load_profile(self):
        base = self._profile_key
        fs = str(self._settings.value(f"{base}/sample_rate", "1e5"))
        bcount = int(self._settings.value(f"{base}/buffer_count", 10))
        tdiv = str(self._settings.value(f"{base}/time_div", "1 ms/div"))
        ch1v = str(self._settings.value(f"{base}/ch1_vdiv", "500 mV/div"))
        ch2v = str(self._settings.value(f"{base}/ch2_vdiv", "500 mV/div"))
        ch1off = float(self._settings.value(f"{base}/ch1_offset_v", 0.0))
        ch2off = float(self._settings.value(f"{base}/ch2_offset_v", 0.0))
        autoscale = str(self._settings.value(f"{base}/autoscale", "false")).lower() in (
            "1",
            "true",
            "yes",
        )
        upmode = str(self._settings.value(f"{base}/update_mode", "repeated"))
        tmode = str(self._settings.value(f"{base}/trigger_mode", "auto"))
        tsrc = str(self._settings.value(f"{base}/trigger_source", "ch1"))
        tedge = str(self._settings.value(f"{base}/trigger_edge", "rising"))
        tlevel = float(self._settings.value(f"{base}/trigger_level", 0.0))
        ch2 = str(self._settings.value(f"{base}/ch2_enable", "false")).lower() in (
            "1",
            "true",
            "yes",
        )

        if self.sample_rate.findText(fs) >= 0:
            self.sample_rate.setCurrentText(fs)
        self.buffer_count.setValue(max(1, min(64, bcount)))
        if self.time_div.findText(tdiv) >= 0:
            self.time_div.setCurrentText(tdiv)
        if self.ch1_vdiv.findText(ch1v) >= 0:
            self.ch1_vdiv.setCurrentText(ch1v)
        if self.ch2_vdiv.findText(ch2v) >= 0:
            self.ch2_vdiv.setCurrentText(ch2v)
        self.ch1_offset.setValue(ch1off)
        self.ch2_offset.setValue(ch2off)
        self.autoscale_chk.setChecked(autoscale)
        if self.update_mode.findText(upmode) >= 0:
            self.update_mode.setCurrentText(upmode)
        if self.trigger_mode.findText(tmode) >= 0:
            self.trigger_mode.setCurrentText(tmode)
        if self.trigger_source.findText(tsrc) >= 0:
            self.trigger_source.setCurrentText(tsrc)
        if self.trigger_edge.findText(tedge) >= 0:
            self.trigger_edge.setCurrentText(tedge)
        self.trigger_level.setValue(tlevel)
        self.ch2_enable.setChecked(ch2)


class SuppliesPanel(QWidget):
    """Supplies panel for AD power rails with master enable and live monitor."""

    def __init__(self, tool: InstrumentTool, backend: DiscoveryBackendAdapter):
        super().__init__()
        self.tool = tool
        self.backend = backend
        self._settings = QSettings("NodeZilla", "NodeZilla")
        self._profile_key = f"instruments/{tool.key}"

        layout = QVBoxLayout(self)
        self.runtime = QLabel()
        self.state_label = QLabel("Master: Off")

        top = QHBoxLayout()
        self.master_enable = QCheckBox("Master Enable")
        self.apply_btn = QPushButton("Apply")
        self.refresh_btn = QPushButton("Refresh")
        top.addWidget(self.master_enable)
        top.addWidget(self.apply_btn)
        top.addWidget(self.refresh_btn)
        top.addStretch(1)

        body = QHBoxLayout()

        rails = QFrame()
        rails.setFrameShape(QFrame.StyledPanel)
        rails_form = QFormLayout(rails)
        self.v_pos = QDoubleSpinBox()
        self.v_pos.setRange(0.0, 5.0)
        self.v_pos.setDecimals(3)
        self.v_pos.setSingleStep(0.1)
        self.v_pos.setValue(1.0)
        self.v_neg = QDoubleSpinBox()
        self.v_neg.setRange(-5.0, 0.0)
        self.v_neg.setDecimals(3)
        self.v_neg.setSingleStep(0.1)
        self.v_neg.setValue(-1.0)
        self.tracking = QCheckBox("Symmetrical rails (V- = -V+)")
        self.power_limit = QComboBox()
        self.power_limit.addItems(["1.0", "1.5", "2.0", "2.5", "3.0"])
        self.power_limit.setCurrentText("2.5")
        rails_form.addRow("V+ (V)", self.v_pos)
        rails_form.addRow("V- (V)", self.v_neg)
        rails_form.addRow(self.tracking)
        rails_form.addRow("Power Limit (W)", self.power_limit)

        mon = QFrame()
        mon.setFrameShape(QFrame.StyledPanel)
        mon_form = QFormLayout(mon)
        self.m_vp = QLabel("--")
        self.m_vn = QLabel("--")
        self.m_usb_v = QLabel("--")
        self.m_usb_i = QLabel("--")
        self.m_temp = QLabel("--")
        mon_form.addRow("V+ Meas", self.m_vp)
        mon_form.addRow("V- Meas", self.m_vn)
        mon_form.addRow("USB Voltage", self.m_usb_v)
        mon_form.addRow("USB Current", self.m_usb_i)
        mon_form.addRow("Temperature", self.m_temp)

        body.addWidget(rails, 1)
        body.addWidget(mon, 1)

        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(110)

        layout.addWidget(self.runtime)
        layout.addWidget(self.state_label)
        layout.addLayout(top)
        layout.addLayout(body, 1)
        layout.addWidget(self.notes)

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(400)
        self.poll_timer.timeout.connect(self._refresh_status)
        self.recfg_timer = QTimer(self)
        self.recfg_timer.setSingleShot(True)
        self.recfg_timer.setInterval(300)
        self.recfg_timer.timeout.connect(lambda: self._apply_config(quiet=True))

        self.apply_btn.clicked.connect(self._apply_config)
        self.refresh_btn.clicked.connect(self._refresh_status)
        self.master_enable.toggled.connect(self._save_profile)
        self.v_pos.valueChanged.connect(self._save_profile)
        self.v_neg.valueChanged.connect(self._save_profile)
        self.tracking.toggled.connect(self._save_profile)
        self.power_limit.currentTextChanged.connect(self._save_profile)
        self.tracking.toggled.connect(self._sync_symmetry_ui)
        self.master_enable.toggled.connect(self._schedule_apply)
        self.v_pos.valueChanged.connect(self._schedule_apply)
        self.v_neg.valueChanged.connect(self._schedule_apply)
        self.tracking.toggled.connect(self._schedule_apply)
        self.power_limit.currentTextChanged.connect(self._schedule_apply)

        self._load_profile()
        self._sync_symmetry_ui()
        self._update_runtime_line()
        self._refresh_status()
        self.poll_timer.start()

    def _params(self) -> dict:
        return {
            "master_enabled": bool(self.master_enable.isChecked()),
            "v_pos_v": float(self.v_pos.value()),
            "v_neg_v": float(self.v_neg.value()),
            "tracking": bool(self.tracking.isChecked()),
            "power_limit_w": float(self.power_limit.currentText()),
        }

    def _apply_config(self, quiet: bool = False):
        p = self._params()
        if p["tracking"]:
            p["v_neg_v"] = -abs(p["v_pos_v"])
            self.v_neg.blockSignals(True)
            self.v_neg.setValue(float(p["v_neg_v"]))
            self.v_neg.blockSignals(False)
        ok, msg = self.backend.configure_supplies(**p)
        if not quiet or not ok:
            self.notes.append(msg)
        self.state_label.setText("Master: On" if p["master_enabled"] else "Master: Off")
        self._refresh_status()
        return ok

    def _refresh_status(self):
        ok, msg, st = self.backend.read_supplies_status()
        if not ok:
            return
        self.m_vp.setText(f"{float(st.get('v_pos_meas_v', 0.0)):.3f} V")
        self.m_vn.setText(f"{float(st.get('v_neg_meas_v', 0.0)):.3f} V")
        self.m_usb_v.setText(f"{float(st.get('usb_voltage_v', 0.0)):.3f} V")
        self.m_usb_i.setText(f"{float(st.get('usb_current_a', 0.0)) * 1e3:.1f} mA")
        self.m_temp.setText(f"{float(st.get('temperature_c', 0.0)):.1f} C")

    def sync_from_backend(self):
        """Pull backend supplies state into controls and monitor labels."""
        ok, _msg, st = self.backend.read_supplies_status()
        if not ok:
            return
        self.master_enable.blockSignals(True)
        self.v_pos.blockSignals(True)
        self.v_neg.blockSignals(True)
        self.tracking.blockSignals(True)
        self.power_limit.blockSignals(True)
        self.master_enable.setChecked(bool(st.get("master_enabled", False)))
        self.v_pos.setValue(float(st.get("v_pos_v", self.v_pos.value())))
        self.v_neg.setValue(float(st.get("v_neg_v", self.v_neg.value())))
        self.tracking.setChecked(bool(st.get("tracking", False)))
        lim = f"{float(st.get('power_limit_w', 2.5)):.1f}"
        if self.power_limit.findText(lim) >= 0:
            self.power_limit.setCurrentText(lim)
        self.master_enable.blockSignals(False)
        self.v_pos.blockSignals(False)
        self.v_neg.blockSignals(False)
        self.tracking.blockSignals(False)
        self.power_limit.blockSignals(False)
        self._sync_symmetry_ui()
        self.state_label.setText(
            "Master: On" if bool(st.get("master_enabled", False)) else "Master: Off"
        )
        self._refresh_status()

    def _schedule_apply(self, *_args):
        self.recfg_timer.start()

    def _sync_symmetry_ui(self):
        sym = bool(self.tracking.isChecked())
        self.v_neg.setEnabled(not sym)
        if sym:
            self.v_neg.blockSignals(True)
            self.v_neg.setValue(-abs(float(self.v_pos.value())))
            self.v_neg.blockSignals(False)

    def _update_runtime_line(self):
        dev = self.backend.connected_device()
        if dev:
            self.runtime.setText(f"Runtime: {self.backend.backend_name()} | Device: {dev}")
        else:
            self.runtime.setText(f"Runtime: {self.backend.backend_name()} | Device: Disconnected")

    def on_connection_changed(self):
        self._update_runtime_line()
        if self.backend.connected_device() is not None:
            self._refresh_status()

    def _save_profile(self, *_args):
        base = self._profile_key
        self._settings.setValue(f"{base}/master", bool(self.master_enable.isChecked()))
        self._settings.setValue(f"{base}/v_pos", float(self.v_pos.value()))
        self._settings.setValue(f"{base}/v_neg", float(self.v_neg.value()))
        self._settings.setValue(f"{base}/tracking", bool(self.tracking.isChecked()))
        self._settings.setValue(f"{base}/limit", self.power_limit.currentText())

    def _load_profile(self):
        base = self._profile_key
        m = str(self._settings.value(f"{base}/master", "false")).lower() in ("1", "true", "yes")
        vp = float(self._settings.value(f"{base}/v_pos", 1.0))
        vn = float(self._settings.value(f"{base}/v_neg", -1.0))
        tr = str(self._settings.value(f"{base}/tracking", "false")).lower() in ("1", "true", "yes")
        lim = str(self._settings.value(f"{base}/limit", "2.5"))
        self.master_enable.setChecked(m)
        self.v_pos.setValue(max(0.0, min(5.0, vp)))
        self.v_neg.setValue(max(-5.0, min(0.0, vn)))
        self.tracking.setChecked(tr)
        if self.power_limit.findText(lim) >= 0:
            self.power_limit.setCurrentText(lim)


class StaticIOPanel(QWidget):
    """Static IO panel with live LED view and optional manual switch control."""

    def __init__(self, tool: InstrumentTool, backend: DiscoveryBackendAdapter):
        super().__init__()
        self.tool = tool
        self.backend = backend
        self._updating = False
        self._last_mask = 0
        self._sequence_active = False

        root = QVBoxLayout(self)
        self.runtime = QLabel()
        self.state = QLabel("Static IO")
        top = QHBoxLayout()
        self.manual_mode = QCheckBox("Manual switches")
        self.refresh_btn = QPushButton("Refresh")
        top.addWidget(self.manual_mode)
        top.addWidget(self.refresh_btn)
        top.addStretch(1)

        cmd = QHBoxLayout()
        self.port_val = QSpinBox()
        self.port_val.setRange(0, 255)
        self.port_btn = QPushButton("PORT")
        self.line_val = QSpinBox()
        self.line_val.setRange(0, 15)
        self.line_btn = QPushButton("LINE")
        self.delay_ms = QSpinBox()
        self.delay_ms.setRange(1, 1000)
        self.delay_ms.setValue(1)
        self.reset_btn = QPushButton("RESET")
        self.strobe_btn = QPushButton("STROBE")
        cmd.addWidget(QLabel("PORT"))
        cmd.addWidget(self.port_val)
        cmd.addWidget(self.port_btn)
        cmd.addSpacing(12)
        cmd.addWidget(QLabel("LINE"))
        cmd.addWidget(self.line_val)
        cmd.addWidget(self.line_btn)
        cmd.addSpacing(12)
        cmd.addWidget(QLabel("Delay (ms)"))
        cmd.addWidget(self.delay_ms)
        cmd.addWidget(self.reset_btn)
        cmd.addWidget(self.strobe_btn)
        cmd.addStretch(1)

        grid_frame = QFrame()
        grid_frame.setFrameShape(QFrame.StyledPanel)
        grid = QGridLayout(grid_frame)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self._io_leds: Dict[int, QFrame] = {}
        self._io_switches: Dict[int, QToolButton] = {}
        order = [15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
        for idx, io in enumerate(order):
            row = 0 if idx < 8 else 1
            col = idx if idx < 8 else idx - 8
            cell = QFrame()
            cell.setFrameShape(QFrame.StyledPanel)
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(6, 6, 6, 6)
            lbl = QLabel(f"IO{io}")
            led = QFrame()
            led.setFixedSize(22, 22)
            led.setStyleSheet("border-radius: 11px; background: #666; border: 1px solid #444;")
            sw = QToolButton()
            sw.setCheckable(True)
            sw.setText("OFF")
            sw.setEnabled(False)
            sw.toggled.connect(lambda checked, i=io: self._on_switch_toggled(i, checked))
            cell_layout.addWidget(lbl, 0, Qt.AlignHCenter)
            cell_layout.addWidget(led, 0, Qt.AlignHCenter)
            cell_layout.addWidget(sw, 0, Qt.AlignHCenter)
            grid.addWidget(cell, row, col)
            self._io_leds[io] = led
            self._io_switches[io] = sw

        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(100)

        root.addWidget(self.runtime)
        root.addWidget(self.state)
        root.addLayout(top)
        root.addLayout(cmd)
        root.addWidget(grid_frame, 1)
        root.addWidget(self.notes)

        self.refresh_btn.clicked.connect(self._refresh_mask)
        self.manual_mode.toggled.connect(self._on_manual_mode_toggled)
        self.port_btn.clicked.connect(self._run_port)
        self.line_btn.clicked.connect(self._run_line)
        self.reset_btn.clicked.connect(self._run_reset)
        self.strobe_btn.clicked.connect(self._run_strobe)

        self.poll = QTimer(self)
        self.poll.setInterval(120)
        self.poll.timeout.connect(self._refresh_mask)
        self.poll.start()

        self._update_runtime_line()
        self._refresh_mask()

    def _set_led(self, io: int, on: bool):
        led = self._io_leds[io]
        color = "#d63a3a" if on else "#666"
        led.setStyleSheet(f"border-radius: 11px; background: {color}; border: 1px solid #444;")
        sw = self._io_switches[io]
        sw.blockSignals(True)
        sw.setChecked(on)
        sw.setText("ON" if on else "OFF")
        sw.blockSignals(False)

    def _apply_mask_to_ui(self, mask: int):
        self._updating = True
        for io in range(16):
            on = bool((mask >> io) & 0x1)
            self._set_led(io, on)
        self._updating = False

    def _refresh_mask(self):
        ok, msg, mask = self.backend.digitalio_read_mask()
        if not ok:
            return
        self._last_mask = int(mask) & 0xFFFF
        self._apply_mask_to_ui(self._last_mask)

    def _on_manual_mode_toggled(self, enabled: bool):
        for sw in self._io_switches.values():
            sw.setEnabled(enabled)
        self.state.setText("Static IO (manual)" if enabled else "Static IO (monitor)")

    def _on_switch_toggled(self, io: int, checked: bool):
        if self._updating or not self.manual_mode.isChecked() or self._sequence_active:
            return
        if checked:
            self._last_mask |= 1 << io
        else:
            self._last_mask &= ~(1 << io)
        ok, msg = self.backend.digitalio_write_mask(self._last_mask)
        if not ok:
            self.notes.append(msg)
        self._refresh_mask()

    def _run_port(self):
        if self._sequence_active:
            return
        ok, msg = self.backend.PORT(int(self.port_val.value()))
        self.notes.append(msg)
        self._refresh_mask()

    def _run_line(self):
        if self._sequence_active:
            return
        ok, msg = self.backend.LINE(int(self.line_val.value()))
        self.notes.append(msg)
        self._refresh_mask()

    def _set_sequence_active(self, active: bool):
        self._sequence_active = bool(active)
        enabled = not self._sequence_active
        self.port_btn.setEnabled(enabled)
        self.line_btn.setEnabled(enabled)
        self.reset_btn.setEnabled(enabled)
        self.strobe_btn.setEnabled(enabled)
        self.refresh_btn.setEnabled(enabled)
        self.manual_mode.setEnabled(enabled)
        self.delay_ms.setEnabled(enabled)

    def _run_reset(self):
        if self._sequence_active:
            return
        d = max(1, int(self.delay_ms.value()))
        self._set_sequence_active(True)

        ok, msg = self.backend.digitalio_write_mask(0)
        if not ok:
            self.notes.append(msg)
            self._set_sequence_active(False)
            return
        self._refresh_mask()

        def step_io11():
            ok2, msg2 = self.backend.digitalio_write_mask(1 << 11)
            if not ok2:
                self.notes.append(msg2)
                self._set_sequence_active(False)
                return
            self._refresh_mask()
            QTimer.singleShot(d, step_final)

        def step_final():
            ok3, msg3 = self.backend.digitalio_write_mask(1 << 12)
            if not ok3:
                self.notes.append(msg3)
            self._refresh_mask()
            self.notes.append(f"RESET(delay_ms={d}) executed.")
            self._set_sequence_active(False)

        QTimer.singleShot(0, step_io11)

    def _run_strobe(self):
        if self._sequence_active:
            return
        d = max(1, int(self.delay_ms.value()))
        base = int(self._last_mask)
        self._set_sequence_active(True)

        def step_on():
            ok1, msg1 = self.backend.digitalio_write_mask(base | (1 << 13) | (1 << 12))
            if not ok1:
                self.notes.append(msg1)
                self._set_sequence_active(False)
                return
            self._refresh_mask()
            QTimer.singleShot(3 * d, step_off)

        def step_off():
            ok2, msg2 = self.backend.digitalio_write_mask((base & ~(1 << 13)) | (1 << 12))
            if not ok2:
                self.notes.append(msg2)
                self._set_sequence_active(False)
                return
            self._refresh_mask()
            QTimer.singleShot(d, step_done)

        def step_done():
            self.notes.append(f"STROBE(delay_ms={d}) executed.")
            self._set_sequence_active(False)

        QTimer.singleShot(d, step_on)

    def _update_runtime_line(self):
        dev = self.backend.connected_device()
        if dev:
            self.runtime.setText(f"Runtime: {self.backend.backend_name()} | Device: {dev}")
        else:
            self.runtime.setText(f"Runtime: {self.backend.backend_name()} | Device: Disconnected")

    def on_connection_changed(self):
        self._update_runtime_line()
        self._refresh_mask()


class InstrumentsTab(QWidget):
    """Instruments workspace for AD tool orchestration and data capture."""

    TOOL_CATALOG = [
        InstrumentTool("wavegen", "Wavegen", "Generate analog stimulus signals."),
        InstrumentTool("scope", "Scope", "Capture and inspect analog waveforms."),
        InstrumentTool("voltmeter", "Voltmeter", "Measure DC/AC node voltages."),
        InstrumentTool("logic", "Logic Analyzer", "Capture digital buses and timing."),
        InstrumentTool("pattern", "Pattern Generator", "Generate digital patterns."),
        InstrumentTool("static", "Static IO", "Set/read static logic states."),
        InstrumentTool("supplies", "Supplies", "Control programmable power rails."),
    ]

    def __init__(
        self,
        backend: Optional[DiscoveryBackendAdapter] = None,
        *,
        show_connection_strip: bool = True,
    ):
        super().__init__()
        self.backend = backend or make_backend()

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # Top connection strip.
        conn_frame = QFrame()
        conn_frame.setFrameShape(QFrame.StyledPanel)
        self.conn_frame = conn_frame
        conn_row = QHBoxLayout(conn_frame)
        conn_row.setContentsMargins(8, 6, 8, 6)

        self.status = QLabel("Hardware: Disconnected")
        self.devices = QComboBox()
        self.refresh_btn = QPushButton("Refresh")
        self.connect_btn = QPushButton("Connect")
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setEnabled(False)

        conn_row.addWidget(self.status, 1)
        conn_row.addWidget(QLabel("Device"))
        conn_row.addWidget(self.devices, 2)
        conn_row.addWidget(self.refresh_btn)
        conn_row.addWidget(self.connect_btn)
        conn_row.addWidget(self.disconnect_btn)
        conn_row.addWidget(QLabel(f"Backend: {self.backend.backend_name()}"))

        root.addWidget(conn_frame)
        self.conn_frame.setVisible(bool(show_connection_strip))

        # Main split: tool list on left, active tool panel on right.
        split = QSplitter()
        self.tool_list = QListWidget()
        self.tool_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tool_defs: Dict[str, InstrumentTool] = {}
        self._subwindows: Dict[str, object] = {}
        self.workspace = QMdiArea()
        self.workspace.setViewMode(QMdiArea.SubWindowView)
        self.workspace.setTabsClosable(False)
        self.workspace.setTabsMovable(True)

        left_col = QWidget()
        left_col_layout = QVBoxLayout(left_col)
        left_col_layout.setContentsMargins(0, 0, 0, 0)
        left_col_layout.addWidget(QLabel("Tools"))
        left_col_layout.addWidget(self.tool_list, 1)
        action_row = QHBoxLayout()
        self.open_btn = QPushButton("Open")
        self.close_btn = QPushButton("Close")
        self.tile_btn = QPushButton("Tile")
        self.cascade_btn = QPushButton("Cascade")
        action_row.addWidget(self.open_btn)
        action_row.addWidget(self.close_btn)
        action_row.addWidget(self.tile_btn)
        action_row.addWidget(self.cascade_btn)
        left_col_layout.addLayout(action_row)

        for tool in self.TOOL_CATALOG:
            item = QListWidgetItem(tool.name)
            item.setData(1, tool.key)
            self.tool_list.addItem(item)
            self._tool_defs[tool.key] = tool

        split.addWidget(left_col)
        split.addWidget(self.workspace)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 920])
        root.addWidget(split, 1)

        self.refresh_btn.clicked.connect(self.refresh_devices)
        self.connect_btn.clicked.connect(self._connect_selected_device)
        self.disconnect_btn.clicked.connect(self._disconnect_device)
        self.open_btn.clicked.connect(self._open_selected_tool)
        self.close_btn.clicked.connect(self._close_active_tool)
        self.tile_btn.clicked.connect(self.workspace.tileSubWindows)
        self.cascade_btn.clicked.connect(self.workspace.cascadeSubWindows)
        self.tool_list.itemDoubleClicked.connect(self._open_tool_from_item)
        self.backend.connection_changed.connect(self._on_connection_changed)

        self.refresh_devices()
        default_key = "supplies"
        default_row = self._find_tool_row(default_key)
        if default_row < 0 and self.tool_list.count() > 0:
            default_row = 0
        if default_row >= 0:
            self.tool_list.setCurrentRow(default_row)
            item = self.tool_list.item(default_row)
            if item is not None:
                self._open_tool_from_item(item)

    def _find_tool_row(self, tool_key: str) -> int:
        for row in range(self.tool_list.count()):
            item = self.tool_list.item(row)
            if item is not None and item.data(1) == tool_key:
                return row
        return -1

    def _open_tool_from_item(self, item: QListWidgetItem):
        if item is None:
            return
        tool_key = item.data(1)
        if not tool_key:
            return
        self._open_tool(str(tool_key))

    def refresh_devices(self):
        connected = self.backend.connected_device()
        self.devices.clear()
        for dev in self.backend.list_devices():
            self.devices.addItem(dev)
        if self.devices.count() == 0:
            self.devices.addItem("No devices found")
            self.devices.setEnabled(False)
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(bool(connected))
            if connected:
                self.status.setText(f"Hardware: Connected to {connected}.")
        else:
            # Keep connection lock semantics after refresh.
            if connected:
                idx = self.devices.findText(connected)
                if idx >= 0:
                    self.devices.setCurrentIndex(idx)
                self.devices.setEnabled(False)
                self.connect_btn.setEnabled(False)
                self.disconnect_btn.setEnabled(True)
                self.status.setText(f"Hardware: Connected to {connected}.")
            else:
                self.devices.setEnabled(True)
                self.connect_btn.setEnabled(True)
                self.disconnect_btn.setEnabled(False)

    def _connect_selected_device(self):
        if self.devices.count() == 0 or not self.devices.isEnabled():
            return
        dev = self.devices.currentText()
        ok, msg = self.backend.connect_device(dev)
        if not ok:
            self.status.setText(f"Hardware: {msg}")
            return
        self.status.setText(f"Hardware: {msg}")
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.devices.setEnabled(False)

    def _disconnect_device(self):
        ok, msg = self.backend.disconnect_device()
        self.status.setText(f"Hardware: {msg}")
        if ok:
            self.connect_btn.setEnabled(True)
            self.disconnect_btn.setEnabled(False)
            self.devices.setEnabled(True)

    def _open_selected_tool(self):
        row = self.tool_list.currentRow()
        if row < 0:
            return
        item = self.tool_list.item(row)
        if item is None:
            return
        tool_key = item.data(1)
        self._open_tool(tool_key)

    def _open_tool(self, tool_key: str):
        existing = self._subwindows.get(tool_key)
        if existing is not None:
            self.workspace.setActiveSubWindow(existing)
            existing.showNormal()
            return

        tool = self._tool_defs.get(tool_key)
        if tool is None:
            return
        if tool_key == "scope":
            panel = ScopePanel(tool, self.backend)
        elif tool_key == "wavegen":
            panel = WavegenPanel(tool, self.backend)
        elif tool_key == "supplies":
            panel = SuppliesPanel(tool, self.backend)
        elif tool_key == "static":
            panel = StaticIOPanel(tool, self.backend)
        else:
            panel = ToolPanel(
                tool,
                self.backend.start_tool,
                self.backend.stop_tool,
                self.backend.backend_name(),
                self.backend.connected_device,
            )
        sub = self.workspace.addSubWindow(panel)
        sub.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        sub.setWindowTitle(tool.name)
        panel.show()
        sub.resize(520, 360)
        sub.show()
        self._subwindows[tool_key] = sub
        sub.destroyed.connect(lambda _obj=None, k=tool_key: self._subwindows.pop(k, None))

    def _close_active_tool(self):
        sub = self.workspace.activeSubWindow()
        if sub is not None:
            sub.close()

    def _on_connection_changed(self, connected: bool, message: str):
        self.status.setText(f"Hardware: {message}")
        self.connect_btn.setEnabled(not connected)
        self.disconnect_btn.setEnabled(connected)
        for key in list(self._subwindows.keys()):
            sub = self._subwindows.get(key)
            if sub is not None and hasattr(sub, "widget"):
                w = sub.widget()
                if hasattr(w, "on_connection_changed"):
                    w.on_connection_changed()
                elif isinstance(w, ToolPanel):
                    w._update_runtime_line()

    def sync_supplies_panels(self):
        """Refresh any open Supplies panel from current backend state."""
        sub = self._subwindows.get("supplies")
        if sub is None:
            return
        w = sub.widget() if hasattr(sub, "widget") else None
        if w is not None and hasattr(w, "sync_from_backend"):
            w.sync_from_backend()
