"""Diagnostic for a suspicious DC read on an Analog Discovery scope channel.

Takes three captures in a row and prints per-run mean / pk-pk / std / first
samples, so you can tell whether the ADC is actually acquiring or returning
a stuck value.

Wire the signal under test to the chosen channel's differential pair:

    SCOPE_CH=0  ->  1+ (signal),  1- (signal ground)
    SCOPE_CH=1  ->  2+ (signal),  2- (signal ground)

The AD front-end is differential. '-' input must share a ground reference
with whatever is producing the signal; otherwise the reading floats.

Run:

    SCOPE_CH=1 python3 -m scripts.dc_diagnose

Interpretation:

  - std > 0 and mean matches expected voltage within ~50 mV
      -> real ADC capture, wiring good
  - std == 0 exactly, mean identical across all 3 runs
      -> channel not acquiring; input pair is floating or front-end stuck
  - std > 0 but mean way off expected
      -> wiring / reference issue (differential pair not referenced to source
         ground) or wrong probe attenuation
"""

from __future__ import annotations

import os
import time

import numpy as np

from pydwf import (
    DwfAcquisitionMode,
    DwfAnalogCoupling,
    DwfLibrary,
    DwfState,
    DwfTriggerSource,
)
from pydwf.utilities import openDwfDevice


CH = int(os.getenv("SCOPE_CH", "1"))
RANGE_V = float(os.getenv("SCOPE_RANGE", "5.0"))
N_SAMPLES = 8192
SAMPLE_RATE = 1_000_000.0
N_RUNS = 3


def main() -> None:
    with openDwfDevice(DwfLibrary()) as hdwf:
        s = hdwf.analogIn
        s.reset()
        s.channelEnableSet(CH, True)
        s.channelRangeSet(CH, RANGE_V)
        s.channelOffsetSet(CH, 0.0)
        try:
            s.channelCouplingSet(CH, DwfAnalogCoupling.DC)
        except Exception:
            # Some AD variants don't expose coupling control; DC is default.
            pass
        s.frequencySet(SAMPLE_RATE)
        s.bufferSizeSet(N_SAMPLES)
        s.acquisitionModeSet(DwfAcquisitionMode.Single)
        s.triggerSourceSet(DwfTriggerSource.None_)
        time.sleep(0.2)

        print(f"channel={CH}  range={RANGE_V} V  rate={SAMPLE_RATE:.0f} S/s  "
              f"buffer={N_SAMPLES}")

        for i in range(N_RUNS):
            s.configure(False, True)
            while s.status(True) != DwfState.Done:
                time.sleep(0.01)
            x = s.statusData(CH, N_SAMPLES)
            print(
                f"run{i}: "
                f"mean={x.mean():+.4f} V  "
                f"pp={x.max() - x.min():.4f} V  "
                f"std={x.std():.5f} V  "
                f"first5={np.round(x[:5], 4).tolist()}"
            )


if __name__ == "__main__":
    main()
