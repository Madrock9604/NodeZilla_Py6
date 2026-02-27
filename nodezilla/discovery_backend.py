from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
import sys
import time
from ctypes import byref, c_bool, c_byte, c_char, c_double, c_int
from ctypes.util import find_library
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Signal

_KEEP_IO12_ON_MASK = 1 << 12
_PORT_IO_MAP = [0, 1, 2, 3, 7, 8, 14, 15]
_LINE_IO_MAP = [4, 5, 6, 9]
_PORT_IO_BITS_MASK = sum(1 << b for b in _PORT_IO_MAP)
_LINE_IO_BITS_MASK = sum(1 << b for b in _LINE_IO_MAP)


class DiscoveryBackendAdapter(QObject):
    """Backend integration surface for AD2/AD3 and future hardware backends."""

    connection_changed = Signal(bool, str)

    def backend_name(self) -> str:
        return "Unconfigured backend"

    def list_devices(self) -> List[str]:
        return []

    def connect_device(self, device_id: str) -> tuple[bool, str]:
        return False, "No backend implementation configured."

    def disconnect_device(self) -> tuple[bool, str]:
        return False, "No backend implementation configured."

    def connected_device(self) -> Optional[str]:
        return None

    def start_tool(self, tool_key: str) -> tuple[bool, str]:
        return True, f"{tool_key}: start placeholder"

    def stop_tool(self, tool_key: str) -> tuple[bool, str]:
        return True, f"{tool_key}: stop placeholder"

    def configure_scope(
        self,
        sample_rate_hz: float,
        buffer_size: int,
        ch1_range_v: float,
        *,
        trigger_mode: str = "auto",
        trigger_edge: str = "rising",
        trigger_level_v: float = 0.0,
        ch2_enabled: bool = False,
        ch2_range_v: float = 5.0,
        ch1_offset_v: float = 0.0,
        ch2_offset_v: float = 0.0,
    ) -> tuple[bool, str]:
        return False, "Scope configuration not implemented."

    def configure_wavegen(
        self,
        *,
        ch1_enabled: bool = True,
        ch1_waveform: str = "sine",
        ch1_frequency_hz: float = 1e3,
        ch1_amplitude_v: float = 1.0,
        ch1_offset_v: float = 0.0,
        ch2_enabled: bool = False,
        ch2_waveform: str = "sine",
        ch2_frequency_hz: float = 1e3,
        ch2_amplitude_v: float = 1.0,
        ch2_offset_v: float = 0.0,
    ) -> tuple[bool, str]:
        return False, "Wavegen configuration not implemented."

    def configure_supplies(
        self,
        *,
        master_enabled: bool,
        v_pos_v: float,
        v_neg_v: float,
        tracking: bool = False,
        power_limit_w: float = 2.5,
    ) -> tuple[bool, str]:
        return False, "Supplies configuration not implemented."

    def read_supplies_status(self) -> tuple[bool, str, Dict[str, float]]:
        return False, "Supplies status not implemented.", {}

    def read_scope_data(self, max_samples: int) -> tuple[bool, str, List[float]]:
        return False, "Scope data read not implemented.", []

    def read_scope_channels(
        self, max_samples: int
    ) -> tuple[bool, str, Dict[str, List[float]]]:
        ok, msg, ch1 = self.read_scope_data(max_samples)
        if not ok:
            return False, msg, {}
        return True, msg, {"ch1": ch1}

    def PORT(self, value: int) -> tuple[bool, str]:
        return False, "PORT not implemented."

    def LINE(self, value: int) -> tuple[bool, str]:
        return False, "LINE not implemented."

    def RESET(self, delay_ms: int = 1) -> tuple[bool, str]:
        return False, "RESET not implemented."

    def STROBE(self, delay_ms: int = 1) -> tuple[bool, str]:
        return False, "STROBE not implemented."

    def digitalio_write_mask(self, mask: int) -> tuple[bool, str]:
        return False, "Digital IO write not implemented."

    def digitalio_read_mask(self) -> tuple[bool, str, int]:
        return False, "Digital IO read not implemented.", 0


class MockDiscoveryBackend(DiscoveryBackendAdapter):
    """Mock backend used while AD integration is being wired."""

    def __init__(self):
        super().__init__()
        self._connected: Optional[str] = None
        self._scope_phase = 0.0
        self._dio_mask = _KEEP_IO12_ON_MASK
        self._wavegen_cfg = {
            "ch1_enabled": True,
            "ch1_waveform": "sine",
            "ch1_frequency_hz": 1e3,
            "ch1_amplitude_v": 1.0,
            "ch1_offset_v": 0.0,
            "ch2_enabled": False,
            "ch2_waveform": "sine",
            "ch2_frequency_hz": 1e3,
            "ch2_amplitude_v": 1.0,
            "ch2_offset_v": 0.0,
        }
        self._supplies_cfg = {
            "master_enabled": False,
            "v_pos_v": 1.0,
            "v_neg_v": -1.0,
            "tracking": False,
            "power_limit_w": 2.5,
        }
        self._supplies_recovering = False
        self._supplies_recovering = False
        self._supplies_recovering = False

    def backend_name(self) -> str:
        return "Mock"

    def list_devices(self) -> List[str]:
        return ["Analog Discovery 2 (Mock)", "Analog Discovery 3 (Mock)"]

    def connect_device(self, device_id: str) -> tuple[bool, str]:
        self._connected = device_id
        self.connection_changed.emit(True, f"Connected to {device_id}.")
        return True, f"Connected to {device_id}."

    def disconnect_device(self) -> tuple[bool, str]:
        if self._connected is None:
            return False, "No instrument is connected."
        device = self._connected
        self._supplies_cfg["master_enabled"] = False
        self._supplies_cfg["v_pos_v"] = 0.0
        self._supplies_cfg["v_neg_v"] = 0.0
        self._dio_mask = 0
        self._connected = None
        self.connection_changed.emit(False, f"Disconnected from {device}.")
        return True, f"Disconnected from {device}."

    def connected_device(self) -> Optional[str]:
        return self._connected

    def start_tool(self, tool_key: str) -> tuple[bool, str]:
        if self._connected is None:
            return False, "Connect a device first."
        if tool_key == "supplies":
            self._supplies_cfg["master_enabled"] = True
            return True, "Supplies master enabled (mock)."
        return True, f"{tool_key}: started (mock)"

    def stop_tool(self, tool_key: str) -> tuple[bool, str]:
        if self._connected is None:
            return False, "Connect a device first."
        if tool_key == "supplies":
            self._supplies_cfg["master_enabled"] = False
            self._supplies_cfg["v_pos_v"] = 0.0
            self._supplies_cfg["v_neg_v"] = 0.0
            self._dio_mask = 0
            return True, "Supplies master disabled (mock)."
        return True, f"{tool_key}: stopped (mock)"

    def configure_scope(
        self,
        sample_rate_hz: float,
        buffer_size: int,
        ch1_range_v: float,
        *,
        trigger_mode: str = "auto",
        trigger_edge: str = "rising",
        trigger_level_v: float = 0.0,
        ch2_enabled: bool = False,
        ch2_range_v: float = 5.0,
        ch1_offset_v: float = 0.0,
        ch2_offset_v: float = 0.0,
    ) -> tuple[bool, str]:
        if self._connected is None:
            return False, "Connect a device first."
        return True, (
            f"Scope configured (mock): fs={sample_rate_hz:.0f} Hz, "
            f"buf={buffer_size}, ch1={ch1_range_v:.2f}V@{ch1_offset_v:.2f}V, "
            f"ch2={ch2_range_v:.2f}V@{ch2_offset_v:.2f}V, "
            f"trig={trigger_mode}/{trigger_edge}@{trigger_level_v:.2f}V, "
            f"ch2={'on' if ch2_enabled else 'off'}"
        )

    def configure_wavegen(
        self,
        *,
        ch1_enabled: bool = True,
        ch1_waveform: str = "sine",
        ch1_frequency_hz: float = 1e3,
        ch1_amplitude_v: float = 1.0,
        ch1_offset_v: float = 0.0,
        ch2_enabled: bool = False,
        ch2_waveform: str = "sine",
        ch2_frequency_hz: float = 1e3,
        ch2_amplitude_v: float = 1.0,
        ch2_offset_v: float = 0.0,
    ) -> tuple[bool, str]:
        if self._connected is None:
            return False, "Connect a device first."
        self._wavegen_cfg = {
            "ch1_enabled": bool(ch1_enabled),
            "ch1_waveform": str(ch1_waveform),
            "ch1_frequency_hz": float(ch1_frequency_hz),
            "ch1_amplitude_v": float(ch1_amplitude_v),
            "ch1_offset_v": float(ch1_offset_v),
            "ch2_enabled": bool(ch2_enabled),
            "ch2_waveform": str(ch2_waveform),
            "ch2_frequency_hz": float(ch2_frequency_hz),
            "ch2_amplitude_v": float(ch2_amplitude_v),
            "ch2_offset_v": float(ch2_offset_v),
        }
        return True, (
            "Wavegen configured (mock): "
            f"CH1={'on' if ch1_enabled else 'off'} {ch1_waveform} "
            f"{ch1_frequency_hz:.3f}Hz {ch1_amplitude_v:.3f}V {ch1_offset_v:.3f}V, "
            f"CH2={'on' if ch2_enabled else 'off'} {ch2_waveform} "
            f"{ch2_frequency_hz:.3f}Hz {ch2_amplitude_v:.3f}V {ch2_offset_v:.3f}V"
        )

    def configure_supplies(
        self,
        *,
        master_enabled: bool,
        v_pos_v: float,
        v_neg_v: float,
        tracking: bool = False,
        power_limit_w: float = 2.5,
    ) -> tuple[bool, str]:
        if self._connected is None:
            return False, "Connect a device first."
        if not master_enabled:
            v_pos_v = 0.0
            v_neg_v = 0.0
        self._supplies_cfg = {
            "master_enabled": bool(master_enabled),
            "v_pos_v": float(v_pos_v),
            "v_neg_v": float(v_neg_v),
            "tracking": bool(tracking),
            "power_limit_w": float(power_limit_w),
        }
        return True, (
            "Supplies configured (mock): "
            f"master={'on' if master_enabled else 'off'}, "
            f"V+={float(v_pos_v):.3f}V, V-={float(v_neg_v):.3f}V, "
            f"tracking={'on' if tracking else 'off'}, "
            f"limit={float(power_limit_w):.2f}W"
        )

    def read_supplies_status(self) -> tuple[bool, str, Dict[str, float]]:
        if self._connected is None:
            return False, "Connect a device first.", {}
        status: Dict[str, float] = dict(self._supplies_cfg)
        status["usb_voltage_v"] = 4.98
        status["usb_current_a"] = 0.72 if self._supplies_cfg["master_enabled"] else 0.12
        status["v_pos_meas_v"] = float(self._supplies_cfg["v_pos_v"]) * (0.998 if self._supplies_cfg["master_enabled"] else 0.0)
        status["v_neg_meas_v"] = float(self._supplies_cfg["v_neg_v"]) * (0.998 if self._supplies_cfg["master_enabled"] else 0.0)
        status["temperature_c"] = 41.5
        return True, "Supplies status updated (mock).", status

    def read_scope_data(self, max_samples: int) -> tuple[bool, str, List[float]]:
        if self._connected is None:
            return False, "Connect a device first.", []
        n = max(64, int(max_samples))
        samples = []
        for i in range(n):
            t = self._scope_phase + (2.0 * math.pi * i / n)
            samples.append(1.25 * math.sin(t) + 0.15 * math.sin(3.0 * t))
        self._scope_phase += 0.2
        return True, "Scope samples updated (mock).", samples

    def read_scope_channels(
        self, max_samples: int
    ) -> tuple[bool, str, Dict[str, List[float]]]:
        ok, msg, ch1 = self.read_scope_data(max_samples)
        if not ok:
            return False, msg, {}
        n = len(ch1)
        ch2 = []
        for i in range(n):
            t = self._scope_phase + (2.0 * math.pi * i / n) + 0.9
            ch2.append(0.85 * math.sin(t))
        return True, msg, {"ch1": ch1, "ch2": ch2}

    @staticmethod
    def _encode_value_to_mask(value: int, io_map: List[int]) -> int:
        mask = 0
        for bit_idx, io_idx in enumerate(io_map):
            if (value >> bit_idx) & 0x1:
                mask |= 1 << io_idx
        return mask

    def PORT(self, value: int) -> tuple[bool, str]:
        if self._connected is None:
            return False, "Connect a device first."
        if not isinstance(value, int) or value < 0 or value > 255:
            return False, "PORT expects an integer from 0 to 255."
        port_mask = self._encode_value_to_mask(value, _PORT_IO_MAP)
        self._dio_mask = (self._dio_mask & ~_PORT_IO_BITS_MASK) | port_mask | _KEEP_IO12_ON_MASK
        return True, f"PORT({value}) executed."

    def LINE(self, value: int) -> tuple[bool, str]:
        if self._connected is None:
            return False, "Connect a device first."
        if not isinstance(value, int) or value < 0 or value > 15:
            return False, "LINE expects an integer from 0 to 15."
        line_mask = self._encode_value_to_mask(value, _LINE_IO_MAP)
        self._dio_mask = (self._dio_mask & ~_LINE_IO_BITS_MASK) | line_mask | _KEEP_IO12_ON_MASK
        return True, f"LINE({value}) executed."

    def RESET(self, delay_ms: int = 1) -> tuple[bool, str]:
        if self._connected is None:
            return False, "Connect a device first."
        d = max(1, int(delay_ms))
        self._dio_mask = 0
        self._dio_mask = 1 << 11
        time.sleep(d / 1000.0)
        self._dio_mask = _KEEP_IO12_ON_MASK
        return True, f"RESET(delay_ms={d}) executed."

    def STROBE(self, delay_ms: int = 1) -> tuple[bool, str]:
        if self._connected is None:
            return False, "Connect a device first."
        d = max(1, int(delay_ms))
        base = self._dio_mask
        time.sleep(d / 1000.0)
        self._dio_mask = base | _KEEP_IO12_ON_MASK | (1 << 13)
        time.sleep((3 * d) / 1000.0)
        self._dio_mask = (self._dio_mask & ~(1 << 13)) | _KEEP_IO12_ON_MASK
        time.sleep(d / 1000.0)
        return True, f"STROBE(delay_ms={d}) executed."

    def digitalio_write_mask(self, mask: int) -> tuple[bool, str]:
        if self._connected is None:
            return False, "Connect a device first."
        self._dio_mask = int(mask) & 0xFFFF
        return True, "Digital IO mask written (mock)."

    def digitalio_read_mask(self) -> tuple[bool, str, int]:
        if self._connected is None:
            return False, "Connect a device first.", 0
        return True, "Digital IO mask read (mock).", int(self._dio_mask) & 0xFFFF


class DwfDiscoveryBackend(DiscoveryBackendAdapter):
    """Digilent WaveForms backend via native `libdwf`."""

    def __init__(self):
        super().__init__()
        self._dwf = self._load_library()
        self._connected: Optional[str] = None
        self._hdwf = c_int(0)
        self._device_index_by_name: Dict[str, int] = {}
        self._scope_sample_rate_hz = 1e5
        self._scope_buffer_size = 1024
        self._scope_ch1_range_v = 5.0
        self._scope_ch2_range_v = 5.0
        self._scope_ch1_offset_v = 0.0
        self._scope_ch2_offset_v = 0.0
        self._scope_configured = False
        self._scope_ch2_enabled = False
        self._wavegen_configured = False
        self._wavegen_cfg = {
            "ch1_enabled": True,
            "ch1_waveform": "sine",
            "ch1_frequency_hz": 1e3,
            "ch1_amplitude_v": 1.0,
            "ch1_offset_v": 0.0,
            "ch2_enabled": False,
            "ch2_waveform": "sine",
            "ch2_frequency_hz": 1e3,
            "ch2_amplitude_v": 1.0,
            "ch2_offset_v": 0.0,
        }
        self._dio_initialized = False
        self._dio_mask = _KEEP_IO12_ON_MASK
        self._supplies_cfg = {
            "master_enabled": False,
            "v_pos_v": 1.0,
            "v_neg_v": -1.0,
            "tracking": False,
            "power_limit_w": 2.5,
        }

    def backend_name(self) -> str:
        return "Digilent DWF"

    def is_available(self) -> bool:
        return self._dwf is not None

    def list_devices(self) -> List[str]:
        if self._dwf is None:
            return []
        count = c_int(0)
        if not self._dwf.FDwfEnum(c_int(0), byref(count)):
            return []
        devices: List[str] = []
        self._device_index_by_name.clear()
        for i in range(count.value):
            name_buf = (c_char * 64)()
            sn_buf = (c_char * 32)()
            self._dwf.FDwfEnumDeviceName(c_int(i), name_buf)
            self._dwf.FDwfEnumSN(c_int(i), sn_buf)
            name = name_buf.value.decode(errors="ignore").strip() or f"Device {i}"
            sn = sn_buf.value.decode(errors="ignore").strip()
            label = f"{name} [{sn}]" if sn else name
            devices.append(label)
            self._device_index_by_name[label] = i
        return devices

    def connect_device(self, device_id: str) -> tuple[bool, str]:
        if self._dwf is None:
            return False, "Digilent runtime (libdwf) not found."
        if self._connected is not None:
            self.disconnect_device()
        idx = self._device_index_by_name.get(device_id)
        if idx is None:
            self.list_devices()
            idx = self._device_index_by_name.get(device_id)
        if idx is None:
            return False, f"Device not found: {device_id}"

        hdwf = c_int(0)
        ok = self._dwf.FDwfDeviceOpen(c_int(idx), byref(hdwf))
        if not ok or hdwf.value == 0:
            return False, f"Open failed: {self._last_error()}"
        self._hdwf = hdwf
        self._connected = device_id
        self._scope_configured = False
        self._scope_ch2_enabled = False
        self._wavegen_configured = False
        self._dio_initialized = False
        self._dio_mask = _KEEP_IO12_ON_MASK
        self.connection_changed.emit(True, f"Connected to {device_id}.")
        return True, f"Connected to {device_id}."

    def disconnect_device(self) -> tuple[bool, str]:
        if self._dwf is None:
            return False, "Digilent runtime (libdwf) not found."
        if self._connected is None:
            return False, "No instrument is connected."
        device = self._connected
        # Best effort: shut rails fully off to 0 V before closing device.
        try:
            cfg = dict(self._supplies_cfg)
            cfg["master_enabled"] = False
            cfg["v_pos_v"] = 0.0
            cfg["v_neg_v"] = 0.0
            self.configure_supplies(**cfg)
        except Exception:
            pass
        try:
            self._dio_force_all_low()
        except Exception:
            pass
        if self._hdwf.value != 0:
            self._dwf.FDwfDeviceClose(self._hdwf)
        self._hdwf = c_int(0)
        self._connected = None
        self._scope_configured = False
        self._scope_ch2_enabled = False
        self._wavegen_configured = False
        self._supplies_cfg["master_enabled"] = False
        self._supplies_cfg["v_pos_v"] = 0.0
        self._supplies_cfg["v_neg_v"] = 0.0
        self._dio_initialized = False
        self._dio_mask = 0
        self.connection_changed.emit(False, f"Disconnected from {device}.")
        return True, f"Disconnected from {device}."

    @staticmethod
    def _encode_value_to_mask(value: int, io_map: List[int]) -> int:
        mask = 0
        for bit_idx, io_idx in enumerate(io_map):
            if (value >> bit_idx) & 0x1:
                mask |= 1 << io_idx
        return mask

    def _dio_init(self) -> tuple[bool, str]:
        if self._dwf is None:
            return False, "Digilent runtime (libdwf) not found."
        if self._connected is None or self._hdwf.value == 0:
            return False, "Connect a device first."
        required = (
            "FDwfDigitalIOOutputEnableSet",
            "FDwfDigitalIOOutputSet",
            "FDwfDigitalIOConfigure",
        )
        if not all(hasattr(self._dwf, n) for n in required):
            return False, "Digital IO API unavailable in loaded DWF runtime."
        ok = True
        if hasattr(self._dwf, "FDwfDigitalIOReset"):
            ok = bool(ok and self._dwf.FDwfDigitalIOReset(self._hdwf))
        ok = bool(ok and self._dwf.FDwfDigitalIOOutputEnableSet(self._hdwf, c_int(0xFFFF)))
        ok = bool(ok and self._dwf.FDwfDigitalIOOutputSet(self._hdwf, c_int(self._dio_mask & 0xFFFF)))
        ok = bool(ok and self._dwf.FDwfDigitalIOConfigure(self._hdwf))
        if not ok:
            return False, f"Digital IO init failed: {self._last_error()}"
        self._dio_initialized = True
        return True, "Digital IO initialized."

    def _dio_write(self, mask: int) -> tuple[bool, str]:
        if not self._dio_initialized:
            ok, msg = self._dio_init()
            if not ok:
                return False, msg
        self._dio_mask = int(mask) & 0xFFFF
        ok = self._dwf.FDwfDigitalIOOutputSet(self._hdwf, c_int(self._dio_mask))
        ok = bool(ok and self._dwf.FDwfDigitalIOConfigure(self._hdwf))
        if not ok:
            return False, f"Digital IO write failed: {self._last_error()}"
        return True, "Digital IO updated."

    def PORT(self, value: int) -> tuple[bool, str]:
        if not isinstance(value, int) or value < 0 or value > 255:
            return False, "PORT expects an integer from 0 to 255."
        port_mask = self._encode_value_to_mask(value, _PORT_IO_MAP)
        mask = (self._dio_mask & ~_PORT_IO_BITS_MASK) | port_mask | _KEEP_IO12_ON_MASK
        ok, msg = self._dio_write(mask)
        if not ok:
            return False, msg
        return True, f"PORT({value}) executed."

    def LINE(self, value: int) -> tuple[bool, str]:
        if not isinstance(value, int) or value < 0 or value > 15:
            return False, "LINE expects an integer from 0 to 15."
        line_mask = self._encode_value_to_mask(value, _LINE_IO_MAP)
        mask = (self._dio_mask & ~_LINE_IO_BITS_MASK) | line_mask | _KEEP_IO12_ON_MASK
        ok, msg = self._dio_write(mask)
        if not ok:
            return False, msg
        return True, f"LINE({value}) executed."

    def RESET(self, delay_ms: int = 1) -> tuple[bool, str]:
        d = max(1, int(delay_ms))
        ok, msg = self._dio_write(0)
        if not ok:
            return False, msg
        ok, msg = self._dio_write(1 << 11)
        if not ok:
            return False, msg
        time.sleep(d / 1000.0)
        ok, msg = self._dio_write(_KEEP_IO12_ON_MASK)
        if not ok:
            return False, msg
        return True, f"RESET(delay_ms={d}) executed."

    def STROBE(self, delay_ms: int = 1) -> tuple[bool, str]:
        d = max(1, int(delay_ms))
        base = self._dio_mask
        time.sleep(d / 1000.0)
        ok, msg = self._dio_write(base | _KEEP_IO12_ON_MASK | (1 << 13))
        if not ok:
            return False, msg
        time.sleep((3 * d) / 1000.0)
        ok, msg = self._dio_write((self._dio_mask & ~(1 << 13)) | _KEEP_IO12_ON_MASK)
        if not ok:
            return False, msg
        time.sleep(d / 1000.0)
        return True, f"STROBE(delay_ms={d}) executed."

    def digitalio_write_mask(self, mask: int) -> tuple[bool, str]:
        return self._dio_write(int(mask) & 0xFFFF)

    def digitalio_read_mask(self) -> tuple[bool, str, int]:
        if not self._dio_initialized:
            ok, msg = self._dio_init()
            if not ok:
                return False, msg, 0
        out_mask = c_int(0)
        if hasattr(self._dwf, "FDwfDigitalIOStatus"):
            ok = self._dwf.FDwfDigitalIOStatus(self._hdwf)
            if not ok:
                return False, f"Digital IO status failed: {self._last_error()}", 0
        if hasattr(self._dwf, "FDwfDigitalIOOutputGet"):
            ok = self._dwf.FDwfDigitalIOOutputGet(self._hdwf, byref(out_mask))
            if ok:
                self._dio_mask = int(out_mask.value) & 0xFFFF
                return True, "Digital IO mask read.", self._dio_mask
        return True, "Digital IO mask read (cached).", int(self._dio_mask) & 0xFFFF

    def connected_device(self) -> Optional[str]:
        return self._connected

    def start_tool(self, tool_key: str) -> tuple[bool, str]:
        if self._dwf is None:
            return False, "Digilent runtime (libdwf) not found."
        if self._connected is None or self._hdwf.value == 0:
            return False, "Connect a device first."
        if tool_key == "scope":
            if not self._scope_configured:
                ok, msg = self.configure_scope(
                    self._scope_sample_rate_hz,
                    self._scope_buffer_size,
                    self._scope_ch1_range_v,
                    ch2_enabled=self._scope_ch2_enabled,
                    ch2_range_v=self._scope_ch2_range_v,
                    ch1_offset_v=self._scope_ch1_offset_v,
                    ch2_offset_v=self._scope_ch2_offset_v,
                )
                if not ok:
                    return False, msg
            ok = self._dwf.FDwfAnalogInConfigure(self._hdwf, c_bool(False), c_bool(True))
            if not ok:
                return False, f"Scope start failed: {self._last_error()}"
            return True, "Scope acquisition started."
        if tool_key == "wavegen":
            if not hasattr(self._dwf, "FDwfAnalogOutConfigure"):
                return False, "Wavegen API unavailable in loaded DWF runtime."
            if not self._wavegen_configured:
                ok, msg = self.configure_wavegen(**self._wavegen_cfg)
                if not ok:
                    return False, msg
            # Default behavior matches WaveForms "No synchronization".
            ok = True
            for ch in (0, 1):
                ok = bool(ok and self._dwf.FDwfAnalogOutConfigure(self._hdwf, c_int(ch), c_bool(True)))
            if not ok:
                return False, f"Wavegen start failed: {self._last_error()}"
            return True, "Wavegen started."
        if tool_key == "supplies":
            cfg = dict(self._supplies_cfg)
            cfg["master_enabled"] = True
            return self.configure_supplies(**cfg)
        return True, f"{tool_key}: no live command yet."

    def stop_tool(self, tool_key: str) -> tuple[bool, str]:
        if self._dwf is None:
            return False, "Digilent runtime (libdwf) not found."
        if self._connected is None or self._hdwf.value == 0:
            return False, "Connect a device first."
        if tool_key == "scope":
            ok = self._dwf.FDwfAnalogInConfigure(self._hdwf, c_bool(False), c_bool(False))
            if not ok:
                return False, f"Scope stop failed: {self._last_error()}"
            return True, "Scope acquisition stopped."
        if tool_key == "wavegen":
            if not hasattr(self._dwf, "FDwfAnalogOutConfigure"):
                return False, "Wavegen API unavailable in loaded DWF runtime."
            ok = True
            for ch in (0, 1):
                ok = bool(ok and self._dwf.FDwfAnalogOutConfigure(self._hdwf, c_int(ch), c_bool(False)))
            if not ok:
                return False, f"Wavegen stop failed: {self._last_error()}"
            return True, "Wavegen stopped."
        if tool_key == "supplies":
            cfg = dict(self._supplies_cfg)
            cfg["master_enabled"] = False
            cfg["v_pos_v"] = 0.0
            cfg["v_neg_v"] = 0.0
            ok, msg = self.configure_supplies(**cfg)
            self._dio_force_all_low()
            return ok, msg
        return True, f"{tool_key}: no live command yet."

    def configure_supplies(
        self,
        *,
        master_enabled: bool,
        v_pos_v: float,
        v_neg_v: float,
        tracking: bool = False,
        power_limit_w: float = 2.5,
    ) -> tuple[bool, str]:
        if self._dwf is None:
            return False, "Digilent runtime (libdwf) not found."
        if self._connected is None or self._hdwf.value == 0:
            return False, "Connect a device first."
        if not master_enabled:
            v_pos_v = 0.0
            v_neg_v = 0.0
        # Best-effort AnalogIO mapping for AD2/AD3.
        if not hasattr(self._dwf, "FDwfAnalogIOEnableSet"):
            self._supplies_cfg = {
                "master_enabled": bool(master_enabled),
                "v_pos_v": float(v_pos_v),
                "v_neg_v": float(v_neg_v),
                "tracking": bool(tracking),
                "power_limit_w": float(power_limit_w),
            }
            return True, "Supplies configured (runtime has no AnalogIO controls; cached only)."

        # Hard-off path: on some devices one rail can linger unless we clear
        # multiple channel/node combinations before disabling AnalogIO.
        if not master_enabled and hasattr(self._dwf, "FDwfAnalogIOChannelNodeSet"):
            ok_off = self._force_supplies_off_hard()
            self._supplies_cfg = {
                "master_enabled": False,
                "v_pos_v": 0.0,
                "v_neg_v": 0.0,
                "tracking": bool(tracking),
                "power_limit_w": float(power_limit_w),
            }
            if ok_off:
                return True, "Supplies disabled: rails forced to 0 V."
            return False, f"Supplies disable failed: {self._last_error()}"

        ok = True
        if hasattr(self._dwf, "FDwfAnalogIOReset"):
            ok = bool(ok and self._dwf.FDwfAnalogIOReset(self._hdwf))
        # Enable AnalogIO engine first on some runtimes/devices.
        ok = bool(ok and self._dwf.FDwfAnalogIOEnableSet(self._hdwf, c_bool(True)))
        wrote_node = False
        if hasattr(self._dwf, "FDwfAnalogIOChannelNodeSet"):
            # AD2/AD3 supplies usually expose per-channel Enable+Voltage nodes.
            # Try both common mappings:
            #   A) node0=Voltage
            #   B) node0=Enable, node1=Voltage
            for ch, vset in ((0, float(v_pos_v)), (1, float(v_neg_v))):
                # Try mapping B first: node0=enable, node1=voltage.
                r_enable = self._dwf.FDwfAnalogIOChannelNodeSet(
                    self._hdwf, c_int(ch), c_int(0), c_double(1.0 if master_enabled else 0.0)
                )
                r_voltage = self._dwf.FDwfAnalogIOChannelNodeSet(
                    self._hdwf, c_int(ch), c_int(1), c_double(vset)
                )
                if r_enable and r_voltage:
                    wrote_node = True
                    continue
                # Fallback mapping A: node0=voltage only.
                r_voltage0 = self._dwf.FDwfAnalogIOChannelNodeSet(
                    self._hdwf, c_int(ch), c_int(0), c_double(vset)
                )
                wrote_node = bool(wrote_node or r_voltage0)
        # Global master gate after channel programming.
        ok = bool(ok and self._dwf.FDwfAnalogIOEnableSet(self._hdwf, c_bool(bool(master_enabled))))
        if hasattr(self._dwf, "FDwfAnalogIOConfigure"):
            ok = bool(ok and self._dwf.FDwfAnalogIOConfigure(self._hdwf))
        ok = bool(ok and (wrote_node or not hasattr(self._dwf, "FDwfAnalogIOChannelNodeSet")))
        if not ok:
            return False, f"Supplies configure failed: {self._last_error()}"

        self._supplies_cfg = {
            "master_enabled": bool(master_enabled),
            "v_pos_v": float(v_pos_v),
            "v_neg_v": float(v_neg_v),
            "tracking": bool(tracking),
            "power_limit_w": float(power_limit_w),
        }
        return True, (
            "Supplies configured: "
            f"master={'on' if master_enabled else 'off'}, "
            f"V+={float(v_pos_v):.3f}V, V-={float(v_neg_v):.3f}V, "
            f"tracking={'on' if tracking else 'off'}, limit={float(power_limit_w):.2f}W"
        )

    def _force_supplies_off_hard(self) -> bool:
        if self._dwf is None or self._connected is None or self._hdwf.value == 0:
            return False
        ok = True
        if hasattr(self._dwf, "FDwfAnalogIOReset"):
            ok = bool(ok and self._dwf.FDwfAnalogIOReset(self._hdwf))
        if hasattr(self._dwf, "FDwfAnalogIOEnableSet"):
            ok = bool(ok and self._dwf.FDwfAnalogIOEnableSet(self._hdwf, c_bool(True)))
        # Clear a broad set of channel/node pairs to 0.
        if hasattr(self._dwf, "FDwfAnalogIOChannelNodeSet"):
            for ch in range(0, 8):
                for node in range(0, 16):
                    try:
                        self._dwf.FDwfAnalogIOChannelNodeSet(self._hdwf, c_int(ch), c_int(node), c_double(0.0))
                    except Exception:
                        continue
        if hasattr(self._dwf, "FDwfAnalogIOConfigure"):
            ok = bool(ok and self._dwf.FDwfAnalogIOConfigure(self._hdwf))
        # Explicitly disable AnalogIO after programming zeros.
        if hasattr(self._dwf, "FDwfAnalogIOEnableSet"):
            ok = bool(ok and self._dwf.FDwfAnalogIOEnableSet(self._hdwf, c_bool(False)))
        if hasattr(self._dwf, "FDwfAnalogIOConfigure"):
            ok = bool(ok and self._dwf.FDwfAnalogIOConfigure(self._hdwf))
        # One more pass improves reliability on some AD3 firmware/runtime combos.
        time.sleep(0.03)
        if hasattr(self._dwf, "FDwfAnalogIOEnableSet"):
            ok = bool(ok and self._dwf.FDwfAnalogIOEnableSet(self._hdwf, c_bool(False)))
        if hasattr(self._dwf, "FDwfAnalogIOConfigure"):
            ok = bool(ok and self._dwf.FDwfAnalogIOConfigure(self._hdwf))
        return bool(ok)

    def _dio_force_all_low(self):
        """Force all digital outputs low (including IO12) to avoid leakage paths."""
        if self._dwf is None or self._connected is None or self._hdwf.value == 0:
            return
        if not all(
            hasattr(self._dwf, n)
            for n in ("FDwfDigitalIOOutputEnableSet", "FDwfDigitalIOOutputSet", "FDwfDigitalIOConfigure")
        ):
            return
        if hasattr(self._dwf, "FDwfDigitalIOReset"):
            self._dwf.FDwfDigitalIOReset(self._hdwf)
        self._dwf.FDwfDigitalIOOutputEnableSet(self._hdwf, c_int(0xFFFF))
        self._dwf.FDwfDigitalIOOutputSet(self._hdwf, c_int(0))
        self._dwf.FDwfDigitalIOConfigure(self._hdwf)
        self._dio_mask = 0

    def read_supplies_status(self) -> tuple[bool, str, Dict[str, float]]:
        if self._dwf is None:
            return False, "Digilent runtime (libdwf) not found.", {}
        if self._connected is None or self._hdwf.value == 0:
            return False, "Connect a device first.", {}
        status: Dict[str, float] = dict(self._supplies_cfg)
        if hasattr(self._dwf, "FDwfAnalogIOStatus"):
            try:
                self._dwf.FDwfAnalogIOStatus(self._hdwf)
            except Exception:
                pass
        if hasattr(self._dwf, "FDwfAnalogIOChannelNodeStatus"):
            vp0 = c_double(0.0)
            vn0 = c_double(0.0)
            vp1 = c_double(0.0)
            vn1 = c_double(0.0)
            ok0p = self._dwf.FDwfAnalogIOChannelNodeStatus(self._hdwf, c_int(0), c_int(0), byref(vp0))
            ok0n = self._dwf.FDwfAnalogIOChannelNodeStatus(self._hdwf, c_int(1), c_int(0), byref(vn0))
            ok1p = self._dwf.FDwfAnalogIOChannelNodeStatus(self._hdwf, c_int(0), c_int(1), byref(vp1))
            ok1n = self._dwf.FDwfAnalogIOChannelNodeStatus(self._hdwf, c_int(1), c_int(1), byref(vn1))
            # Prefer node1 as voltage when available; fallback to node0.
            if ok1p:
                status["v_pos_meas_v"] = float(vp1.value)
            elif ok0p:
                status["v_pos_meas_v"] = float(vp0.value)
            if ok1n:
                status["v_neg_meas_v"] = float(vn1.value)
            elif ok0n:
                status["v_neg_meas_v"] = float(vn0.value)
        status.setdefault("v_pos_meas_v", float(status.get("v_pos_v", 0.0)))
        status.setdefault("v_neg_meas_v", float(status.get("v_neg_v", 0.0)))
        status.setdefault("usb_voltage_v", 5.0)
        status.setdefault("usb_current_a", 0.0)
        status.setdefault("temperature_c", 0.0)
        # Keep rails at configured setpoints when supplies are enabled.
        # This is a best-effort auto-recover path for devices/runtimes that
        # occasionally drift or drop AnalogIO settings.
        if (
            bool(self._supplies_cfg.get("master_enabled", False))
            and not bool(getattr(self, "_supplies_recovering", False))
        ):
            vp_t = float(self._supplies_cfg.get("v_pos_v", 0.0))
            vn_t = float(self._supplies_cfg.get("v_neg_v", 0.0))
            vp_m = float(status.get("v_pos_meas_v", vp_t))
            vn_m = float(status.get("v_neg_meas_v", vn_t))
            tol_v = 0.2
            if abs(vp_m - vp_t) > tol_v or abs(vn_m - vn_t) > tol_v:
                self._supplies_recovering = True
                try:
                    self.configure_supplies(**self._supplies_cfg)
                finally:
                    self._supplies_recovering = False
        return True, "Supplies status updated.", status

    def configure_wavegen(
        self,
        *,
        ch1_enabled: bool = True,
        ch1_waveform: str = "sine",
        ch1_frequency_hz: float = 1e3,
        ch1_amplitude_v: float = 1.0,
        ch1_offset_v: float = 0.0,
        ch2_enabled: bool = False,
        ch2_waveform: str = "sine",
        ch2_frequency_hz: float = 1e3,
        ch2_amplitude_v: float = 1.0,
        ch2_offset_v: float = 0.0,
    ) -> tuple[bool, str]:
        if self._dwf is None:
            return False, "Digilent runtime (libdwf) not found."
        if self._connected is None or self._hdwf.value == 0:
            return False, "Connect a device first."
        required = (
            "FDwfAnalogOutNodeEnableSet",
            "FDwfAnalogOutNodeFunctionSet",
            "FDwfAnalogOutNodeFrequencySet",
            "FDwfAnalogOutNodeAmplitudeSet",
            "FDwfAnalogOutNodeOffsetSet",
        )
        if not all(hasattr(self._dwf, name) for name in required):
            return False, "Wavegen API unavailable in loaded DWF runtime."

        wave_fn = {
            "dc": 0,
            "sine": 1,
            "square": 2,
            "triangle": 3,
            "sawtooth": 4,
        }

        def _cfg_channel(enabled: bool, waveform: str, freq: float, amp: float, offs: float, ch: int) -> bool:
            fn = wave_fn.get(str(waveform).lower(), 1)
            freq = float(max(0.001, freq))
            amp = float(max(0.0, amp))
            okc = self._dwf.FDwfAnalogOutNodeEnableSet(self._hdwf, c_int(ch), c_int(0), c_bool(enabled))
            okc = bool(okc and self._dwf.FDwfAnalogOutNodeFunctionSet(self._hdwf, c_int(ch), c_int(0), c_int(fn)))
            okc = bool(okc and self._dwf.FDwfAnalogOutNodeFrequencySet(self._hdwf, c_int(ch), c_int(0), c_double(freq)))
            okc = bool(okc and self._dwf.FDwfAnalogOutNodeAmplitudeSet(self._hdwf, c_int(ch), c_int(0), c_double(amp)))
            okc = bool(okc and self._dwf.FDwfAnalogOutNodeOffsetSet(self._hdwf, c_int(ch), c_int(0), c_double(offs)))
            return bool(okc)

        ok = _cfg_channel(ch1_enabled, ch1_waveform, ch1_frequency_hz, ch1_amplitude_v, ch1_offset_v, 0)
        ok = bool(ok and _cfg_channel(ch2_enabled, ch2_waveform, ch2_frequency_hz, ch2_amplitude_v, ch2_offset_v, 1))
        if not ok:
            return False, f"Wavegen configure failed: {self._last_error()}"

        self._wavegen_cfg = {
            "ch1_enabled": bool(ch1_enabled),
            "ch1_waveform": str(ch1_waveform),
            "ch1_frequency_hz": float(ch1_frequency_hz),
            "ch1_amplitude_v": float(ch1_amplitude_v),
            "ch1_offset_v": float(ch1_offset_v),
            "ch2_enabled": bool(ch2_enabled),
            "ch2_waveform": str(ch2_waveform),
            "ch2_frequency_hz": float(ch2_frequency_hz),
            "ch2_amplitude_v": float(ch2_amplitude_v),
            "ch2_offset_v": float(ch2_offset_v),
        }
        self._wavegen_configured = True
        return True, (
            "Wavegen configured: "
            f"CH1={'on' if ch1_enabled else 'off'} {ch1_waveform} "
            f"{float(ch1_frequency_hz):.3f}Hz {float(ch1_amplitude_v):.3f}V {float(ch1_offset_v):.3f}V, "
            f"CH2={'on' if ch2_enabled else 'off'} {ch2_waveform} "
            f"{float(ch2_frequency_hz):.3f}Hz {float(ch2_amplitude_v):.3f}V {float(ch2_offset_v):.3f}V"
        )

    def configure_scope(
        self,
        sample_rate_hz: float,
        buffer_size: int,
        ch1_range_v: float,
        *,
        trigger_mode: str = "auto",
        trigger_edge: str = "rising",
        trigger_level_v: float = 0.0,
        ch2_enabled: bool = False,
        ch2_range_v: float = 5.0,
        ch1_offset_v: float = 0.0,
        ch2_offset_v: float = 0.0,
    ) -> tuple[bool, str]:
        if self._dwf is None:
            return False, "Digilent runtime (libdwf) not found."
        if self._connected is None or self._hdwf.value == 0:
            return False, "Connect a device first."
        sample_rate_hz = float(max(1e3, sample_rate_hz))
        buffer_size = int(max(64, min(8192, buffer_size)))
        ch1_range_v = float(max(0.1, ch1_range_v))
        ch2_range_v = float(max(0.1, ch2_range_v))
        ok = self._dwf.FDwfAnalogInReset(self._hdwf)
        ok = bool(ok and self._dwf.FDwfAnalogInChannelEnableSet(self._hdwf, c_int(0), c_bool(True)))
        ok = bool(ok and self._dwf.FDwfAnalogInChannelRangeSet(self._hdwf, c_int(0), c_double(ch1_range_v)))
        if hasattr(self._dwf, "FDwfAnalogInChannelOffsetSet"):
            ok = bool(ok and self._dwf.FDwfAnalogInChannelOffsetSet(self._hdwf, c_int(0), c_double(ch1_offset_v)))
        ok = bool(ok and self._dwf.FDwfAnalogInChannelEnableSet(self._hdwf, c_int(1), c_bool(ch2_enabled)))
        if ch2_enabled:
            ok = bool(ok and self._dwf.FDwfAnalogInChannelRangeSet(self._hdwf, c_int(1), c_double(ch2_range_v)))
            if hasattr(self._dwf, "FDwfAnalogInChannelOffsetSet"):
                ok = bool(ok and self._dwf.FDwfAnalogInChannelOffsetSet(self._hdwf, c_int(1), c_double(ch2_offset_v)))
        ok = bool(ok and self._dwf.FDwfAnalogInFrequencySet(self._hdwf, c_double(sample_rate_hz)))
        ok = bool(ok and self._dwf.FDwfAnalogInBufferSizeSet(self._hdwf, c_int(buffer_size)))
        # Trigger wiring: best-effort. Some runtimes may not expose all symbols.
        try:
            if hasattr(self._dwf, "FDwfAnalogInTriggerAutoTimeoutSet"):
                timeout = c_double(0.0 if trigger_mode.lower() == "normal" else 1.0)
                ok = bool(ok and self._dwf.FDwfAnalogInTriggerAutoTimeoutSet(self._hdwf, timeout))
            if hasattr(self._dwf, "FDwfAnalogInTriggerLevelSet"):
                ok = bool(ok and self._dwf.FDwfAnalogInTriggerLevelSet(self._hdwf, c_double(trigger_level_v)))
            if hasattr(self._dwf, "FDwfAnalogInTriggerConditionSet"):
                cond = c_int(1 if trigger_edge.lower() == "rising" else 0)
                ok = bool(ok and self._dwf.FDwfAnalogInTriggerConditionSet(self._hdwf, cond))
            if hasattr(self._dwf, "FDwfAnalogInTriggerSourceSet"):
                # 2 maps to detector analog-in in DWF enum.
                ok = bool(ok and self._dwf.FDwfAnalogInTriggerSourceSet(self._hdwf, c_int(2)))
        except Exception:
            # Keep scope usable even when trigger symbols vary by platform/runtime.
            pass
        if not ok:
            return False, f"Scope configure failed: {self._last_error()}"
        self._scope_sample_rate_hz = sample_rate_hz
        self._scope_buffer_size = buffer_size
        self._scope_ch1_range_v = ch1_range_v
        self._scope_ch2_range_v = ch2_range_v
        self._scope_ch1_offset_v = ch1_offset_v
        self._scope_ch2_offset_v = ch2_offset_v
        self._scope_ch2_enabled = bool(ch2_enabled)
        self._scope_configured = True
        return True, (
            f"Scope configured: fs={sample_rate_hz:.0f} Hz, "
            f"buf={buffer_size}, ch1={ch1_range_v:.2f}V@{ch1_offset_v:.2f}V, "
            f"ch2={ch2_range_v:.2f}V@{ch2_offset_v:.2f}V, "
            f"trig={trigger_mode}/{trigger_edge}@{trigger_level_v:.2f}V, "
            f"ch2={'on' if ch2_enabled else 'off'}"
        )

    def read_scope_data(self, max_samples: int) -> tuple[bool, str, List[float]]:
        ok, msg, channels = self.read_scope_channels(max_samples)
        if not ok:
            return False, msg, []
        return True, msg, channels.get("ch1", [])

    def read_scope_channels(
        self, max_samples: int
    ) -> tuple[bool, str, Dict[str, List[float]]]:
        if self._dwf is None:
            return False, "Digilent runtime (libdwf) not found.", {}
        if self._connected is None or self._hdwf.value == 0:
            return False, "Connect a device first.", {}
        count = int(max(64, min(self._scope_buffer_size, max_samples)))
        status = c_byte(0)
        ok = self._dwf.FDwfAnalogInStatus(self._hdwf, c_bool(True), byref(status))
        if not ok:
            return False, f"Scope status failed: {self._last_error()}", {}
        out: Dict[str, List[float]] = {}
        arr1 = (c_double * count)()
        ok = self._dwf.FDwfAnalogInStatusData(self._hdwf, c_int(0), arr1, c_int(count))
        if not ok:
            return False, f"Scope CH1 read failed: {self._last_error()}", {}
        out["ch1"] = list(arr1)
        if self._scope_ch2_enabled:
            arr2 = (c_double * count)()
            ok = self._dwf.FDwfAnalogInStatusData(self._hdwf, c_int(1), arr2, c_int(count))
            if not ok:
                return False, f"Scope CH2 read failed: {self._last_error()}", {}
            out["ch2"] = list(arr2)
        return True, "Scope samples updated.", out

    def _last_error(self) -> str:
        if self._dwf is None:
            return "Unknown error"
        buf = (c_char * 512)()
        try:
            self._dwf.FDwfGetLastErrorMsg(buf)
            return buf.value.decode(errors="ignore").strip() or "Unknown error"
        except Exception:
            return "Unknown error"

    def _load_library(self):
        def _log(msg: str):
            try:
                p = Path.home() / "Library" / "Logs" / "NodeZilla" / "startup.log"
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("a", encoding="utf-8") as f:
                    f.write(f"{msg}\n")
            except Exception:
                pass

        exe = Path(sys.executable).resolve()
        app_macos_dir = exe.parent
        app_contents_dir = app_macos_dir.parent
        app_resources_dir = app_contents_dir / "Resources"
        app_frameworks_dir = app_contents_dir / "Frameworks"
        env_lib = os.environ.get("NODEZILLA_DWF_LIB", "").strip()
        candidates = [
            env_lib,
            str(app_macos_dir / "dwf"),
            str(app_macos_dir / "libdwf.dylib"),
            str(app_macos_dir / "dwf.framework" / "dwf"),
            str(app_resources_dir / "dwf"),
            str(app_resources_dir / "libdwf.dylib"),
            str(app_resources_dir / "dwf.framework" / "dwf"),
            str(app_frameworks_dir / "dwf"),
            str(app_frameworks_dir / "libdwf.dylib"),
            str(app_frameworks_dir / "dwf.framework" / "dwf"),
            find_library("dwf"),
            "/Library/Frameworks/dwf.framework/dwf",
            "/usr/local/lib/libdwf.dylib",
            "libdwf.dylib",
            "dwf.dll",
            "libdwf.so",
        ]
        _log("[DWF] probing runtime candidates")
        for path in candidates:
            if not path:
                continue
            try:
                lib = ctypes.cdll.LoadLibrary(path)
                self._configure_signatures(lib)
                _log(f"[DWF] loaded runtime: {path}")
                return lib
            except Exception as e:
                _log(f"[DWF] failed candidate: {path} :: {e}")
                continue
        _log("[DWF] runtime not found; falling back to Mock backend")
        return None

    @staticmethod
    def _configure_signatures(lib):
        lib.FDwfEnum.argtypes = [c_int, ctypes.POINTER(c_int)]
        lib.FDwfEnum.restype = c_bool
        lib.FDwfEnumDeviceName.argtypes = [c_int, ctypes.POINTER(c_char)]
        lib.FDwfEnumDeviceName.restype = c_bool
        lib.FDwfEnumSN.argtypes = [c_int, ctypes.POINTER(c_char)]
        lib.FDwfEnumSN.restype = c_bool
        lib.FDwfDeviceOpen.argtypes = [c_int, ctypes.POINTER(c_int)]
        lib.FDwfDeviceOpen.restype = c_bool
        lib.FDwfDeviceClose.argtypes = [c_int]
        lib.FDwfDeviceClose.restype = c_bool
        lib.FDwfGetLastErrorMsg.argtypes = [ctypes.POINTER(c_char)]
        lib.FDwfGetLastErrorMsg.restype = c_bool
        lib.FDwfAnalogInConfigure.argtypes = [c_int, c_bool, c_bool]
        lib.FDwfAnalogInConfigure.restype = c_bool
        lib.FDwfAnalogInReset.argtypes = [c_int]
        lib.FDwfAnalogInReset.restype = c_bool
        lib.FDwfAnalogInChannelEnableSet.argtypes = [c_int, c_int, c_bool]
        lib.FDwfAnalogInChannelEnableSet.restype = c_bool
        lib.FDwfAnalogInChannelRangeSet.argtypes = [c_int, c_int, c_double]
        lib.FDwfAnalogInChannelRangeSet.restype = c_bool
        lib.FDwfAnalogInFrequencySet.argtypes = [c_int, c_double]
        lib.FDwfAnalogInFrequencySet.restype = c_bool
        lib.FDwfAnalogInBufferSizeSet.argtypes = [c_int, c_int]
        lib.FDwfAnalogInBufferSizeSet.restype = c_bool
        lib.FDwfAnalogInStatus.argtypes = [c_int, c_bool, ctypes.POINTER(c_byte)]
        lib.FDwfAnalogInStatus.restype = c_bool
        lib.FDwfAnalogInStatusData.argtypes = [c_int, c_int, ctypes.POINTER(c_double), c_int]
        lib.FDwfAnalogInStatusData.restype = c_bool
        if hasattr(lib, "FDwfAnalogInTriggerSourceSet"):
            lib.FDwfAnalogInTriggerSourceSet.argtypes = [c_int, c_int]
            lib.FDwfAnalogInTriggerSourceSet.restype = c_bool
        if hasattr(lib, "FDwfAnalogInTriggerLevelSet"):
            lib.FDwfAnalogInTriggerLevelSet.argtypes = [c_int, c_double]
            lib.FDwfAnalogInTriggerLevelSet.restype = c_bool
        if hasattr(lib, "FDwfAnalogInTriggerConditionSet"):
            lib.FDwfAnalogInTriggerConditionSet.argtypes = [c_int, c_int]
            lib.FDwfAnalogInTriggerConditionSet.restype = c_bool
        if hasattr(lib, "FDwfAnalogInTriggerAutoTimeoutSet"):
            lib.FDwfAnalogInTriggerAutoTimeoutSet.argtypes = [c_int, c_double]
            lib.FDwfAnalogInTriggerAutoTimeoutSet.restype = c_bool
        if hasattr(lib, "FDwfAnalogInChannelOffsetSet"):
            lib.FDwfAnalogInChannelOffsetSet.argtypes = [c_int, c_int, c_double]
            lib.FDwfAnalogInChannelOffsetSet.restype = c_bool
        if hasattr(lib, "FDwfAnalogOutConfigure"):
            lib.FDwfAnalogOutConfigure.argtypes = [c_int, c_int, c_bool]
            lib.FDwfAnalogOutConfigure.restype = c_bool
        if hasattr(lib, "FDwfAnalogOutNodeEnableSet"):
            lib.FDwfAnalogOutNodeEnableSet.argtypes = [c_int, c_int, c_int, c_bool]
            lib.FDwfAnalogOutNodeEnableSet.restype = c_bool
        if hasattr(lib, "FDwfAnalogOutNodeFunctionSet"):
            lib.FDwfAnalogOutNodeFunctionSet.argtypes = [c_int, c_int, c_int, c_int]
            lib.FDwfAnalogOutNodeFunctionSet.restype = c_bool
        if hasattr(lib, "FDwfAnalogOutNodeFrequencySet"):
            lib.FDwfAnalogOutNodeFrequencySet.argtypes = [c_int, c_int, c_int, c_double]
            lib.FDwfAnalogOutNodeFrequencySet.restype = c_bool
        if hasattr(lib, "FDwfAnalogOutNodeAmplitudeSet"):
            lib.FDwfAnalogOutNodeAmplitudeSet.argtypes = [c_int, c_int, c_int, c_double]
            lib.FDwfAnalogOutNodeAmplitudeSet.restype = c_bool
        if hasattr(lib, "FDwfAnalogOutNodeOffsetSet"):
            lib.FDwfAnalogOutNodeOffsetSet.argtypes = [c_int, c_int, c_int, c_double]
            lib.FDwfAnalogOutNodeOffsetSet.restype = c_bool
        if hasattr(lib, "FDwfDigitalIOReset"):
            lib.FDwfDigitalIOReset.argtypes = [c_int]
            lib.FDwfDigitalIOReset.restype = c_bool
        if hasattr(lib, "FDwfDigitalIOOutputEnableSet"):
            lib.FDwfDigitalIOOutputEnableSet.argtypes = [c_int, c_int]
            lib.FDwfDigitalIOOutputEnableSet.restype = c_bool
        if hasattr(lib, "FDwfDigitalIOOutputSet"):
            lib.FDwfDigitalIOOutputSet.argtypes = [c_int, c_int]
            lib.FDwfDigitalIOOutputSet.restype = c_bool
        if hasattr(lib, "FDwfDigitalIOConfigure"):
            lib.FDwfDigitalIOConfigure.argtypes = [c_int]
            lib.FDwfDigitalIOConfigure.restype = c_bool
        if hasattr(lib, "FDwfDigitalIOStatus"):
            lib.FDwfDigitalIOStatus.argtypes = [c_int]
            lib.FDwfDigitalIOStatus.restype = c_bool
        if hasattr(lib, "FDwfDigitalIOOutputGet"):
            lib.FDwfDigitalIOOutputGet.argtypes = [c_int, ctypes.POINTER(c_int)]
            lib.FDwfDigitalIOOutputGet.restype = c_bool
        if hasattr(lib, "FDwfAnalogIOReset"):
            lib.FDwfAnalogIOReset.argtypes = [c_int]
            lib.FDwfAnalogIOReset.restype = c_bool
        if hasattr(lib, "FDwfAnalogIOConfigure"):
            lib.FDwfAnalogIOConfigure.argtypes = [c_int]
            lib.FDwfAnalogIOConfigure.restype = c_bool
        if hasattr(lib, "FDwfAnalogIOEnableSet"):
            lib.FDwfAnalogIOEnableSet.argtypes = [c_int, c_bool]
            lib.FDwfAnalogIOEnableSet.restype = c_bool
        if hasattr(lib, "FDwfAnalogIOStatus"):
            lib.FDwfAnalogIOStatus.argtypes = [c_int]
            lib.FDwfAnalogIOStatus.restype = c_bool
        if hasattr(lib, "FDwfAnalogIOChannelNodeSet"):
            lib.FDwfAnalogIOChannelNodeSet.argtypes = [c_int, c_int, c_int, c_double]
            lib.FDwfAnalogIOChannelNodeSet.restype = c_bool
        if hasattr(lib, "FDwfAnalogIOChannelNodeStatus"):
            lib.FDwfAnalogIOChannelNodeStatus.argtypes = [c_int, c_int, c_int, ctypes.POINTER(c_double)]
            lib.FDwfAnalogIOChannelNodeStatus.restype = c_bool


def make_backend() -> DiscoveryBackendAdapter:
    """Create a real DWF backend when available, otherwise fallback to mock."""
    dwf = DwfDiscoveryBackend()
    if dwf.is_available():
        return dwf
    return MockDiscoveryBackend()
