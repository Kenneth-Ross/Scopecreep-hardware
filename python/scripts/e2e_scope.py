"""End-to-end oscilloscope hardware validation (no AI, no REST).

Exercises the exact driver surface the agent's ``scope_capture`` tool uses:

    scope.configure_channel(...)  ->  scope.arm_trigger(...)  ->  scope.read_samples(...)

If this script passes, the full hardware path is proven:

    WaveFormsAnalogDiscovery  ->  pydwf  ->  dwf.framework  ->  USB  ->  AD  ->
    samples[]  ->  stats  ->  pass/fail verdict

Usage
-----
Wire the signal to the chosen scope channel's differential pair:

    SCOPE_CH=0  ->  1+ (signal),  1- (signal ground)
    SCOPE_CH=1  ->  2+ (signal),  2- (signal ground)

Three built-in test cases. Select with TEST=<name>:

    TEST=dc_rail        Requires an external DC source on the probe.
                        EXPECTED_V=<volts> sets the expected value (default 3.3).
                        TOLERANCE=<volts> sets +/- bound (default 0.1).

    TEST=awg_loopback   No external wiring beyond W1 -> <channel>+ and
                        GND -> <channel>-. The AD generates its own 1 kHz
                        2 Vpp sine and checks it round-trips.

    TEST=zero_floor     Short <channel>+ to <channel>-. Expects ~0 V.

Run:
    SCOPE_CH=1 TEST=dc_rail EXPECTED_V=3.3 python3 -m scripts.e2e_scope
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import numpy as np

# We import the adapter lazily inside main() so a missing pydwf install
# gives a readable error instead of a traceback from module load.


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CH = int(os.getenv("SCOPE_CH", "1"))
TEST = os.getenv("TEST", "dc_rail")
EXPECTED_V = float(os.getenv("EXPECTED_V", "3.3"))
TOLERANCE_V = float(os.getenv("TOLERANCE", "0.1"))
SAMPLE_RATE = float(os.getenv("SAMPLE_RATE", "1000000"))
N_SAMPLES = int(os.getenv("N_SAMPLES", "8192"))
VOLTAGE_RANGE = float(os.getenv("VOLTAGE_RANGE", "50.0"))  # full-scale p-p; 50 V = +/-25 V


# ---------------------------------------------------------------------------
# Same stats helper the agent uses
# ---------------------------------------------------------------------------

def compute_stats(samples: np.ndarray) -> dict[str, float]:
    return {
        "v_min":  float(samples.min()),
        "v_max":  float(samples.max()),
        "v_mean": float(samples.mean()),
        "v_pp":   float(samples.max() - samples.min()),
        "v_rms":  float(np.sqrt(np.mean(samples ** 2))),
        "v_std":  float(samples.std()),
    }


# ---------------------------------------------------------------------------
# Runs the exact three-call sequence the agent's scope_capture tool performs
# ---------------------------------------------------------------------------

def agent_style_capture(
    scope,
    *,
    channel: int,
    voltage_range: float,
    sample_rate: float,
    num_samples: int,
    trigger_source: str = "none",
    trigger_level: float = 0.0,
    trigger_edge: str = "rising",
) -> dict[str, float]:
    scope.configure_channel(
        channel=channel,
        voltage_range=voltage_range,
        sample_rate=sample_rate,
        num_samples=num_samples,
    )
    scope.arm_trigger(source=trigger_source, level=trigger_level, edge=trigger_edge)
    samples = scope.read_samples(channel)
    stats = compute_stats(samples)
    stats["sample_rate"] = sample_rate
    stats["num_samples"] = num_samples
    return stats


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def test_dc_rail(device: Any) -> tuple[bool, str, dict]:
    """External DC source on the probe; expect mean within tolerance."""
    stats = agent_style_capture(
        device.scope,
        channel=CH,
        voltage_range=VOLTAGE_RANGE,
        sample_rate=SAMPLE_RATE,
        num_samples=N_SAMPLES,
    )
    err = abs(stats["v_mean"] - EXPECTED_V)
    ok = err <= TOLERANCE_V
    detail = (
        f"mean={stats['v_mean']:+.4f} V  "
        f"expected={EXPECTED_V:+.3f} +/- {TOLERANCE_V} V  "
        f"err={err:.4f} V  pp={stats['v_pp']:.4f} V"
    )
    return ok, detail, stats


def test_awg_loopback(device: Any) -> tuple[bool, str, dict]:
    """Self-contained: AD generates 1 kHz 2 Vpp on W1, reads it on CH."""
    # We reach through the device to the raw pydwf hdwf for AWG setup,
    # because the agent's OscilloscopeBase interface doesn't expose AWG.
    hdwf = device._hdwf
    from pydwf import DwfAnalogOutFunction, DwfAnalogOutNode

    awg = hdwf.analogOut
    node = DwfAnalogOutNode.Carrier
    awg.reset(0)
    awg.nodeEnableSet(0, node, True)
    awg.nodeFunctionSet(0, node, DwfAnalogOutFunction.Sine)
    awg.nodeFrequencySet(0, node, 1000.0)
    awg.nodeAmplitudeSet(0, node, 1.0)     # 1 V peak = 2 Vpp
    awg.nodeOffsetSet(0, node, 0.0)
    awg.configure(0, True)
    time.sleep(0.2)

    stats = agent_style_capture(
        device.scope,
        channel=CH,
        voltage_range=5.0,   # +/-2.5 V covers a 2 Vpp sine around 0
        sample_rate=SAMPLE_RATE,
        num_samples=N_SAMPLES,
    )

    awg.reset(0)

    # Check amplitude
    amp_ok = 1.7 <= stats["v_pp"] <= 2.3

    # Check frequency via zero crossings around the mean
    samples = np.array([stats])  # placeholder; re-capture raw for freq calc
    # Re-read from device for freq; cheaper than re-running capture
    raw = device.scope.read_samples(CH)
    centered = raw - raw.mean()
    zc = np.where(np.diff(np.sign(centered)) > 0)[0]
    freq_hz = SAMPLE_RATE / np.mean(np.diff(zc)) if len(zc) > 2 else float("nan")
    freq_ok = 950.0 <= freq_hz <= 1050.0

    ok = amp_ok and freq_ok
    detail = (
        f"pp={stats['v_pp']:.3f} V (expected 2.0 +/- 0.3)  "
        f"freq={freq_hz:.1f} Hz (expected 1000 +/- 50)"
    )
    return ok, detail, stats


def test_zero_floor(device: Any) -> tuple[bool, str, dict]:
    """CH+ shorted to CH-; expect mean near zero and low noise."""
    stats = agent_style_capture(
        device.scope,
        channel=CH,
        voltage_range=VOLTAGE_RANGE,
        sample_rate=SAMPLE_RATE,
        num_samples=N_SAMPLES,
    )
    ok = abs(stats["v_mean"]) < 0.05 and stats["v_pp"] < 0.1
    detail = (
        f"mean={stats['v_mean']:+.5f} V (expected ~0)  "
        f"pp={stats['v_pp']:.5f} V  std={stats['v_std']:.5f} V"
    )
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

    try:
        from drivers.analog_discovery.waveforms_driver import WaveFormsAnalogDiscovery
    except ImportError as exc:
        print(f"FAIL: cannot import adapter: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"test={TEST}  channel={CH}  range={VOLTAGE_RANGE} V  "
          f"rate={SAMPLE_RATE:.0f} S/s  buffer={N_SAMPLES}")

    device = WaveFormsAnalogDiscovery()
    try:
        device.connect()
        print(f"[PASS] device connect")
    except Exception as exc:
        print(f"[FAIL] device connect  ({type(exc).__name__}: {exc})")
        sys.exit(1)

    try:
        ok, detail, stats = TESTS[TEST](device)
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {TEST}  ({detail})")
        print(f"       full stats: {stats}")
        sys.exit(0 if ok else 1)
    except Exception as exc:
        print(f"[FAIL] {TEST} raised {type(exc).__name__}: {exc}")
        sys.exit(1)
    finally:
        device.disconnect()


if __name__ == "__main__":
    main()
