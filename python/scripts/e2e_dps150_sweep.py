"""End-to-end PSU(DPS-150) -> oscilloscope(AD) sweep test.

Drives the bench FNIRSI DPS-150 over serial, measures each setpoint with
the Analog Discovery via the WaveForms adapter, and verifies measured
mean matches setpoint within tolerance.

This exercises the *actual* hardware path a testbench session uses:

    DPS150 (serial)        <-  agent's bench PSU
    WaveFormsOscilloscope  <-  agent's scope_capture

Wiring
------
    DPS-150 (+)  ->  AD scope <channel>+ pin
    DPS-150 (-)  ->  AD scope <channel>-  pin

Select the scope channel with SCOPE_CH (0 = 1+/1-, 1 = 2+/2-).

Port discovery
--------------
The DPS-150 enumerates as a USB CDC serial port. The script auto-detects
the first matching macOS/Linux device. Override with PSU_PORT:

    PSU_PORT=/dev/cu.usbmodem1480DFDC40251 python3 ...

Sweep points default to [0.0, 1.0, 2.0, 3.3, 4.0, 5.0] V. Override with
VOLTAGES="0,1.5,3.3,5".

Safety: the test sets current limit to CURRENT_LIMIT A (default 0.1) and
disables the output on exit, even on error.

Run:
    SCOPE_CH=1 python3 -m scripts.e2e_dps150_sweep
"""

from __future__ import annotations

import glob
import os
import sys
import time
from pathlib import Path

import numpy as np

# Repo layout: scripts/ is under python/, DPS-150 driver is under backend/.
# Add both to sys.path so this script runs from anywhere without an install.
_REPO_ROOT = Path(__file__).resolve().parents[2]
for p in (_REPO_ROOT / "python", _REPO_ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from drivers.analog_discovery.waveforms_driver import WaveFormsAnalogDiscovery
from drivers.dps150 import DPS150  # noqa: E402  (from backend/)


CH = int(os.getenv("SCOPE_CH", "1"))
TOLERANCE_V = float(os.getenv("TOLERANCE", "0.15"))
SETTLE_S = float(os.getenv("SETTLE_S", "0.3"))         # DPS-150 needs time to ramp
CURRENT_LIMIT = float(os.getenv("CURRENT_LIMIT", "0.1"))  # amps; keep low for safety
SAMPLE_RATE = 1_000_000.0
N_SAMPLES = 8192
VOLTAGE_RANGE = 50.0  # AD scope +/- 25 V window

_DEFAULT_VOLTAGES = [0.0, 1.0, 2.0, 3.3, 4.0, 5.0]


def _parse_voltages() -> list[float]:
    raw = os.getenv("VOLTAGES")
    if not raw:
        return _DEFAULT_VOLTAGES
    return [float(x.strip()) for x in raw.split(",") if x.strip()]


def _autodetect_port() -> str:
    override = os.getenv("PSU_PORT")
    if override:
        return override
    candidates: list[str] = []
    # macOS: /dev/cu.usbmodem*
    candidates += glob.glob("/dev/cu.usbmodem*")
    # Linux: /dev/ttyACM*
    candidates += glob.glob("/dev/ttyACM*")
    if not candidates:
        raise RuntimeError(
            "No serial port found. Plug in the DPS-150 or set PSU_PORT=..."
        )
    return sorted(candidates)[0]


def _capture_mean(scope, channel: int) -> tuple[float, float]:
    scope.configure_channel(
        channel=channel,
        voltage_range=VOLTAGE_RANGE,
        sample_rate=SAMPLE_RATE,
        num_samples=N_SAMPLES,
    )
    scope.arm_trigger(source="none")
    samples = scope.read_samples(channel)
    return float(samples.mean()), float(samples.max() - samples.min())


def main() -> None:
    voltages = _parse_voltages()
    port = _autodetect_port()

    print(f"DPS-150 sweep E2E")
    print(f"  psu_port={port}")
    print(f"  scope_ch={CH}  tolerance=+/- {TOLERANCE_V} V  "
          f"current_limit={CURRENT_LIMIT} A")
    print(f"  sweep:    {voltages}")
    print()

    psu = DPS150(port=port)
    scope_dev = WaveFormsAnalogDiscovery()

    try:
        psu.connect()
        print("[PASS] PSU connect")
    except Exception as exc:
        print(f"[FAIL] PSU connect: {type(exc).__name__}: {exc}")
        sys.exit(1)

    try:
        scope_dev.connect()
        print("[PASS] scope connect")
    except Exception as exc:
        print(f"[FAIL] scope connect: {type(exc).__name__}: {exc}")
        psu.disconnect()
        sys.exit(1)

    fails: list[str] = []
    try:
        psu.set_current(CURRENT_LIMIT)
        psu.set_voltage(0.0)
        psu.enable_output(True)
        time.sleep(SETTLE_S)

        header = (f"{'setpoint':>10} {'psu_meas':>10} {'scope':>10} "
                  f"{'error':>9} {'pp':>8}  verdict")
        print()
        print(header)
        print("-" * len(header))

        for target in voltages:
            psu.set_voltage(target)
            time.sleep(SETTLE_S)

            try:
                psu_meas = psu.get_measurements().get("voltage", float("nan"))
            except Exception:
                psu_meas = float("nan")

            scope_meas, pp = _capture_mean(scope_dev.scope, CH)
            err = scope_meas - target
            ok = abs(err) <= TOLERANCE_V
            verdict = "PASS" if ok else "FAIL"
            print(f"{target:>10.3f} {psu_meas:>+10.4f} {scope_meas:>+10.4f} "
                  f"{err:>+9.4f} {pp:>8.4f}  {verdict}")
            if not ok:
                fails.append(
                    f"setpoint={target}  psu_readback={psu_meas:+.4f}  "
                    f"scope={scope_meas:+.4f}  err={err:+.4f}"
                )

    finally:
        # Always disable the output and close both, even on error.
        try:
            psu.set_voltage(0.0)
            psu.enable_output(False)
        except Exception:
            pass
        psu.disconnect()
        scope_dev.disconnect()

    print()
    if fails:
        print(f"[FAIL] {len(fails)}/{len(voltages)} setpoints out of tolerance:")
        for f in fails:
            print(f"  {f}")
        sys.exit(1)

    print(f"[PASS] all {len(voltages)} setpoints within +/- {TOLERANCE_V} V")


if __name__ == "__main__":
    main()
