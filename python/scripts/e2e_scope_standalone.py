"""Self-contained E2E oscilloscope test (no repo clone needed).

Inlines a minimal copy of the WaveFormsOscilloscope adapter so this one
file can be dropped anywhere and run. Exercises the same three-call
sequence (configure / arm / read) the agent's scope_capture tool uses.

Wire to the chosen scope channel's differential pair:

    SCOPE_CH=0  ->  1+ (signal),  1- (signal ground)
    SCOPE_CH=1  ->  2+ (signal),  2- (signal ground)

Tests (set via TEST=<name>):

    dc_rail        External DC source; EXPECTED_V (default 3.3), TOLERANCE (0.1)
    awg_loopback   Self-test: AD generates 1 kHz 2 Vpp on W1, reads it on CH
    zero_floor     CH+ shorted to CH-; expects ~0 V

Run:
    pip install pydwf numpy
    SCOPE_CH=1 TEST=dc_rail EXPECTED_V=3.3 python3 e2e_scope_standalone.py
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

from pydwf import (
    DwfAcquisitionMode,
    DwfAnalogOutFunction,
    DwfAnalogOutNode,
    DwfLibrary,
    DwfState,
    DwfTriggerSlope,
    DwfTriggerSource,
)
from pydwf.utilities import openDwfDevice


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CH = int(os.getenv("SCOPE_CH", "1"))
TEST = os.getenv("TEST", "dc_rail")
EXPECTED_V = float(os.getenv("EXPECTED_V", "3.3"))
TOLERANCE_V = float(os.getenv("TOLERANCE", "0.1"))
SAMPLE_RATE = float(os.getenv("SAMPLE_RATE", "1000000"))
N_SAMPLES = int(os.getenv("N_SAMPLES", "8192"))
VOLTAGE_RANGE = float(os.getenv("VOLTAGE_RANGE", "50.0"))
CAPTURE_TIMEOUT_S = 5.0


# ---------------------------------------------------------------------------
# Inlined adapter: mirrors WaveFormsOscilloscope from the repo
# ---------------------------------------------------------------------------

class InlineScope:
    def __init__(self, hdwf):
        self._s = hdwf.analogIn
        self._n: dict[int, int] = {}

    def configure_channel(self, channel, voltage_range, sample_rate, num_samples):
        self._s.channelEnableSet(channel, True)
        self._s.channelRangeSet(channel, voltage_range)
        self._s.channelOffsetSet(channel, 0.0)
        self._s.frequencySet(sample_rate)
        self._s.bufferSizeSet(num_samples)
        self._s.acquisitionModeSet(DwfAcquisitionMode.Single)
        self._n[channel] = num_samples

    def arm_trigger(self, source="none", level=0.0, edge="rising"):
        if source == "none":
            self._s.triggerSourceSet(DwfTriggerSource.None_)
        elif source in ("ch0", "ch1"):
            trig_ch = 0 if source == "ch0" else 1
            self._s.triggerSourceSet(DwfTriggerSource.DetectorAnalogIn)
            self._s.triggerChannelSet(trig_ch)
            self._s.triggerTypeSet(0)
            self._s.triggerLevelSet(level)
            self._s.triggerConditionSet(
                DwfTriggerSlope.Fall if edge == "falling" else DwfTriggerSlope.Rise
            )
        elif source == "ext":
            self._s.triggerSourceSet(DwfTriggerSource.External1)
        else:
            raise ValueError(f"bad trigger source {source!r}")
        self._s.configure(False, True)

    def read_samples(self, channel):
        if channel not in self._n:
            raise ValueError(f"channel {channel} not configured")
        deadline = time.monotonic() + CAPTURE_TIMEOUT_S
        while self._s.status(True) != DwfState.Done:
            if time.monotonic() > deadline:
                raise TimeoutError("scope acquisition timed out")
            time.sleep(0.005)
        return self._s.statusData(channel, self._n[channel])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def compute_stats(samples: np.ndarray) -> dict:
    return {
        "v_min":  float(samples.min()),
        "v_max":  float(samples.max()),
        "v_mean": float(samples.mean()),
        "v_pp":   float(samples.max() - samples.min()),
        "v_rms":  float(np.sqrt(np.mean(samples ** 2))),
        "v_std":  float(samples.std()),
    }


def agent_style_capture(scope, *, channel, voltage_range, sample_rate, num_samples,
                        trigger_source="none", trigger_level=0.0, trigger_edge="rising"):
    scope.configure_channel(channel=channel, voltage_range=voltage_range,
                            sample_rate=sample_rate, num_samples=num_samples)
    scope.arm_trigger(source=trigger_source, level=trigger_level, edge=trigger_edge)
    return scope.read_samples(channel)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def test_dc_rail(hdwf):
    scope = InlineScope(hdwf)
    samples = agent_style_capture(
        scope, channel=CH, voltage_range=VOLTAGE_RANGE,
        sample_rate=SAMPLE_RATE, num_samples=N_SAMPLES,
    )
    stats = compute_stats(samples)
    err = abs(stats["v_mean"] - EXPECTED_V)
    ok = err <= TOLERANCE_V
    detail = (f"mean={stats['v_mean']:+.4f} V  expected={EXPECTED_V:+.3f} "
              f"+/- {TOLERANCE_V} V  err={err:.4f} V  pp={stats['v_pp']:.4f} V")
    return ok, detail, stats


def test_awg_loopback(hdwf):
    awg = hdwf.analogOut
    node = DwfAnalogOutNode.Carrier
    awg.reset(0)
    awg.nodeEnableSet(0, node, True)
    awg.nodeFunctionSet(0, node, DwfAnalogOutFunction.Sine)
    awg.nodeFrequencySet(0, node, 1000.0)
    awg.nodeAmplitudeSet(0, node, 1.0)
    awg.nodeOffsetSet(0, node, 0.0)
    awg.configure(0, True)
    time.sleep(0.2)

    scope = InlineScope(hdwf)
    samples = agent_style_capture(
        scope, channel=CH, voltage_range=5.0,
        sample_rate=SAMPLE_RATE, num_samples=N_SAMPLES,
    )
    awg.reset(0)

    stats = compute_stats(samples)
    centered = samples - samples.mean()
    zc = np.where(np.diff(np.sign(centered)) > 0)[0]
    freq = SAMPLE_RATE / np.mean(np.diff(zc)) if len(zc) > 2 else float("nan")

    amp_ok = 1.7 <= stats["v_pp"] <= 2.3
    freq_ok = 950.0 <= freq <= 1050.0
    ok = amp_ok and freq_ok
    detail = (f"pp={stats['v_pp']:.3f} V (expect 2.0 +/- 0.3)  "
              f"freq={freq:.1f} Hz (expect 1000 +/- 50)")
    return ok, detail, stats


def test_zero_floor(hdwf):
    scope = InlineScope(hdwf)
    samples = agent_style_capture(
        scope, channel=CH, voltage_range=VOLTAGE_RANGE,
        sample_rate=SAMPLE_RATE, num_samples=N_SAMPLES,
    )
    stats = compute_stats(samples)
    ok = abs(stats["v_mean"]) < 0.05 and stats["v_pp"] < 0.1
    detail = (f"mean={stats['v_mean']:+.5f} V (expect ~0)  "
              f"pp={stats['v_pp']:.5f} V  std={stats['v_std']:.5f} V")
    return ok, detail, stats


TESTS = {
    "dc_rail":      test_dc_rail,
    "awg_loopback": test_awg_loopback,
    "zero_floor":   test_zero_floor,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if TEST not in TESTS:
        print(f"Unknown TEST={TEST!r}. Valid: {list(TESTS)}", file=sys.stderr)
        sys.exit(2)

    print(f"test={TEST}  channel={CH}  range={VOLTAGE_RANGE} V  "
          f"rate={SAMPLE_RATE:.0f} S/s  buffer={N_SAMPLES}")

    try:
        with openDwfDevice(DwfLibrary()) as hdwf:
            print("[PASS] device connect")
            ok, detail, stats = TESTS[TEST](hdwf)
            mark = "PASS" if ok else "FAIL"
            print(f"[{mark}] {TEST}  ({detail})")
            print(f"       full stats: {stats}")
            sys.exit(0 if ok else 1)
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
