# python/executor/drivers.py
from __future__ import annotations

import glob
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class PsuDriver(Protocol):
    def set(self, voltage: float, current_limit: float) -> None: ...
    def output_on(self) -> None: ...
    def output_off(self) -> None: ...
    def close(self) -> None: ...


@runtime_checkable
class ScopeDriver(Protocol):
    def capture(self, channel: str) -> dict[str, float]: ...
    def close(self) -> None: ...


# ---------- DPS-150 adapter ----------

def _load_dps150_class():
    """Load backend/drivers/dps150.py without clashing with python/drivers/."""
    repo_root = Path(__file__).resolve().parents[2]
    dps_path = repo_root / "backend" / "drivers" / "dps150.py"
    base_path = repo_root / "backend" / "drivers" / "base.py"

    if not dps_path.exists():
        raise RuntimeError(f"DPS-150 driver not found at {dps_path}")

    base_spec = importlib.util.spec_from_file_location("_bench_base", base_path)
    base_mod = importlib.util.module_from_spec(base_spec)
    base_spec.loader.exec_module(base_mod)

    # Pre-register a fake `backend.drivers.base` so the relative `from .base import ...`
    # inside dps150.py resolves to our already-loaded base_mod.
    sys.modules.setdefault("backend", ModuleType("backend"))
    sys.modules.setdefault("backend.drivers", ModuleType("backend.drivers"))
    sys.modules["backend.drivers.base"] = base_mod

    dps_spec = importlib.util.spec_from_file_location("backend.drivers.dps150", dps_path)
    dps_mod = importlib.util.module_from_spec(dps_spec)
    dps_spec.loader.exec_module(dps_mod)
    return dps_mod.DPS150


def _auto_detect_dps_port() -> str:
    candidates = (
        glob.glob("/dev/cu.usbmodem*")
        + glob.glob("/dev/tty.usbmodem*")
        + glob.glob("/dev/ttyACM*")
    )
    if not candidates:
        raise RuntimeError("DPS-150 serial port not found. Set PSU_PORT in .env.")
    return candidates[0]


class Dps150Adapter:
    """PsuDriver implementation wrapping the FNIRSI DPS-150."""

    def __init__(self, port: str | None = None):
        DPS150 = _load_dps150_class()
        resolved = port or os.environ.get("PSU_PORT") or _auto_detect_dps_port()
        self._dev = DPS150(resolved)
        self._dev.connect()

    def set(self, voltage: float, current_limit: float) -> None:
        self._dev.set_current(current_limit)
        self._dev.set_voltage(voltage)

    def output_on(self) -> None:
        self._dev.enable_output(True)

    def output_off(self) -> None:
        try:
            self._dev.enable_output(False)
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._dev.disconnect()
        except Exception:
            pass


# ---------- Analog Discovery adapter ----------

class AnalogDiscoveryScopeAdapter:
    """ScopeDriver implementation wrapping the WaveForms driver."""

    def __init__(self, sample_rate: float = 1_000_000.0, num_samples: int = 1024,
                 voltage_range: float = 10.0):
        from drivers.analog_discovery.waveforms_driver import WaveFormsAnalogDiscovery
        self._hw = WaveFormsAnalogDiscovery()
        self._hw.connect()
        self._sample_rate = sample_rate
        self._num_samples = num_samples
        self._voltage_range = voltage_range

    def capture(self, channel: str) -> dict[str, float]:
        ch = 0 if channel.upper() == "CH1" else 1
        scope = self._hw.scope
        scope.configure_channel(
            channel=ch,
            voltage_range=self._voltage_range,
            sample_rate=self._sample_rate,
            num_samples=self._num_samples,
        )
        scope.arm_trigger(source="none", level=0.0, edge="rising")
        samples = np.asarray(scope.read_samples(ch))
        return {
            "v_min":  float(samples.min()),
            "v_max":  float(samples.max()),
            "v_mean": float(samples.mean()),
            "v_pp":   float(samples.max() - samples.min()),
            "v_rms":  float(np.sqrt(np.mean(samples ** 2))),
        }

    def close(self) -> None:
        try:
            self._hw.disconnect()
        except Exception:
            pass
