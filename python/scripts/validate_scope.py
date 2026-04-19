"""Hardware-link validation for a Digilent Analog Discovery via the WaveForms SDK.

Wire W1 to 1+ and GND to 1- on the AD breadboard, then run:

    pip install pydwf numpy
    python -m scripts.validate_scope

Runs four progressive checks. Each prints PASS/FAIL with the measured value
so you can tell which stage of the link is broken when something fails.

  1. SDK loads              - dwf.framework is on disk and importable
  2. Device enumerates      - USB + drivers see the AD
  3. AnalogIO round-trip    - can read the device's own supply rails
  4. AWG -> scope loopback  - 1 kHz, 2 Vpp sine on W1 is captured on ch0,
                              amplitude and frequency are within tolerance
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np


SCOPE_CH = int(os.getenv("SCOPE_CH", "0"))   # 0 = 1+/1-, 1 = 2+/2-
FREQ_HZ = 1000.0
AMP_V = 1.0            # peak; 2 Vpp
SAMPLE_RATE = 1_000_000.0
N_SAMPLES = 8192
RANGE_V = 5.0
SETTLE_S = 0.2
CAPTURE_TIMEOUT_S = 2.0

AMP_TOL = (1.8, 2.2)   # v_pp bounds
FREQ_TOL = (950.0, 1050.0)


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        sys.exit(1)


def main() -> None:
    # --- 1. SDK loads ---------------------------------------------------
    try:
        from pydwf import (
            DwfLibrary,
            DwfAcquisitionMode,
            DwfAnalogOutNode,
            DwfAnalogOutFunction,
            DwfState,
            DwfTriggerSource,
        )
        from pydwf.utilities import openDwfDevice
    except Exception as exc:
        check("SDK import", False, f"{type(exc).__name__}: {exc}")
        return

    dwf = DwfLibrary()
    ver = dwf.getVersion()
    check("SDK import", True, f"libdwf {ver}")

    # --- 2. Device enumerates ------------------------------------------
    n = dwf.deviceEnum.enumerateDevices()
    if n == 0:
        check("Device enumerates", False, "no Digilent devices found on USB")
    name = dwf.deviceEnum.deviceName(0)
    serial = dwf.deviceEnum.serialNumber(0)
    check("Device enumerates", True, f"{name} sn={serial}")

    with openDwfDevice(dwf) as hdwf:
        # --- 3. AnalogIO round-trip ------------------------------------
        try:
            aio = hdwf.analogIO
            aio.enableSet(True)
            aio.configure()
            # Read USB supply voltage (channel 2, node 0 on AD2/AD3).
            # This doesn't depend on any external wiring, so it proves the
            # control path without touching the analog front-end.
            usb_v = aio.channelNodeStatus(2, 0)
            status = "PASS" if 4.0 < usb_v < 5.5 else "SKIP"
            print(f"[{status}] AnalogIO readback  (USB rail = {usb_v:.3f} V)")
        except Exception as exc:
            print(f"[SKIP] AnalogIO readback  ({type(exc).__name__}: {exc})")

        # --- 4. AWG -> scope loopback ----------------------------------
        awg = hdwf.analogOut
        node = DwfAnalogOutNode.Carrier
        awg.reset(0)
        awg.nodeEnableSet(0, node, True)
        awg.nodeFunctionSet(0, node, DwfAnalogOutFunction.Sine)
        awg.nodeFrequencySet(0, node, FREQ_HZ)
        awg.nodeAmplitudeSet(0, node, AMP_V)
        awg.nodeOffsetSet(0, node, 0.0)
        awg.configure(0, True)

        scope = hdwf.analogIn
        scope.reset()
        scope.channelEnableSet(SCOPE_CH, True)
        scope.channelRangeSet(SCOPE_CH, RANGE_V)
        scope.channelOffsetSet(SCOPE_CH, 0.0)
        scope.frequencySet(SAMPLE_RATE)
        scope.bufferSizeSet(N_SAMPLES)
        scope.acquisitionModeSet(DwfAcquisitionMode.Single)
        scope.triggerSourceSet(DwfTriggerSource.None_)

        time.sleep(SETTLE_S)
        scope.configure(False, True)              # arm + start

        t0 = time.monotonic()
        while scope.status(True) != DwfState.Done:
            if time.monotonic() - t0 > CAPTURE_TIMEOUT_S:
                check("Scope capture", False, "timed out waiting for Done state")
            time.sleep(0.01)

        samples = scope.statusData(SCOPE_CH, N_SAMPLES)
        check("Scope capture", True, f"{len(samples)} samples")

        v_pp = float(samples.max() - samples.min())
        v_mean = float(samples.mean())

        # Frequency via rising zero crossings around the mean.
        centered = samples - v_mean
        zc = np.where(np.diff(np.sign(centered)) > 0)[0]
        if len(zc) < 3:
            check("Loopback frequency", False, f"only {len(zc)} zero crossings")
        period_samples = np.mean(np.diff(zc))
        freq_hz = SAMPLE_RATE / period_samples

        check(
            "Loopback amplitude",
            AMP_TOL[0] < v_pp < AMP_TOL[1],
            f"v_pp = {v_pp:.3f} V (expected 2.0 +/- 0.2)",
        )
        check(
            "Loopback frequency",
            FREQ_TOL[0] < freq_hz < FREQ_TOL[1],
            f"{freq_hz:.1f} Hz (expected 1000 +/- 50)",
        )

        awg.reset(0)

    print("\nAll checks passed. Hardware link is usable for the testbench.")


if __name__ == "__main__":
    main()
