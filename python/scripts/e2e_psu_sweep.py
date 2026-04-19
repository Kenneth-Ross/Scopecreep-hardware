"""End-to-end PSU -> oscilloscope sweep test.

Exercises both hardware adapters together:

  WaveFormsPowerSupply.set_voltage / enable   (agent's psu_configure path)
  WaveFormsOscilloscope.configure_channel / arm_trigger / read_samples
                                              (agent's scope_capture path)

For each target voltage, the PSU is programmed, the scope captures, and
the measured mean is compared against the setpoint. Pass = every step is
within tolerance.

Wiring
------
  AD flywire V+    ->  <channel>+ pin      (red wire labelled V+)
  AD flywire GND   ->  <channel>-  pin     (any black GND wire)

Select the scope channel with SCOPE_CH (0 = 1+/1-, 1 = 2+/2-).

Sweep points default to [0.0, 1.0, 2.0, 3.3, 4.0, 5.0] V. Override with
VOLTAGES="0,1.5,3.3,5".

Run:
    SCOPE_CH=1 python3 -m scripts.e2e_psu_sweep
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

from drivers.analog_discovery.waveforms_driver import WaveFormsAnalogDiscovery


CH = int(os.getenv("SCOPE_CH", "1"))
TOLERANCE_V = float(os.getenv("TOLERANCE", "0.15"))
SETTLE_S = float(os.getenv("SETTLE_S", "0.1"))
SAMPLE_RATE = 1_000_000.0
N_SAMPLES = 8192
VOLTAGE_RANGE = 50.0   # +/-25 V window; safe for any 0-5 V setpoint
PSU_CHANNEL = 0        # 0 = V+ on the Analog Discovery

_DEFAULT_VOLTAGES = [0.0, 1.0, 2.0, 3.3, 4.0, 5.0]


def _parse_voltages() -> list[float]:
    raw = os.getenv("VOLTAGES")
    if not raw:
        return _DEFAULT_VOLTAGES
    return [float(x.strip()) for x in raw.split(",") if x.strip()]


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

    print(f"PSU sweep E2E   scope_ch={CH}  psu_ch={PSU_CHANNEL} (V+)  "
          f"tolerance=+/- {TOLERANCE_V} V")
    print(f"sweep points:   {voltages}")
    print()

    device = WaveFormsAnalogDiscovery()
    device.connect()

    fails: list[str] = []
    try:
        # Enable V+ rail; starts at 0 V until set_voltage is called.
        device.psu.enable(PSU_CHANNEL, True)

        header = f"{'setpoint':>10} {'measured':>10} {'error':>9} {'pp':>8}  verdict"
        print(header)
        print("-" * len(header))

        for target in voltages:
            device.psu.set_voltage(PSU_CHANNEL, target)
            time.sleep(SETTLE_S)

            measured, pp = _capture_mean(device.scope, CH)
            err = measured - target
            ok = abs(err) <= TOLERANCE_V
            verdict = "PASS" if ok else "FAIL"
            print(f"{target:>10.3f} {measured:>+10.4f} {err:>+9.4f} {pp:>8.4f}  {verdict}")
            if not ok:
                fails.append(f"setpoint={target} measured={measured:+.4f} err={err:+.4f}")

        # Leave the rail off and at 0 V when done.
        device.psu.set_voltage(PSU_CHANNEL, 0.0)
        device.psu.enable(PSU_CHANNEL, False)

    finally:
        device.disconnect()

    print()
    if fails:
        print(f"[FAIL] {len(fails)}/{len(voltages)} setpoints out of tolerance:")
        for f in fails:
            print(f"  {f}")
        sys.exit(1)

    print(f"[PASS] all {len(voltages)} setpoints within +/- {TOLERANCE_V} V")


if __name__ == "__main__":
    main()
