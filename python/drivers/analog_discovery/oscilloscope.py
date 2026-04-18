"""Oscilloscope / digitizer instrument module for the Digilent Analog Discovery.

Implements :class:`~python.drivers.base.OscilloscopeBase` using PTI commands.
"""

from __future__ import annotations

import struct

import numpy as np

from ..base import OscilloscopeBase
from .pti import PTIController, scope_configure, scope_arm, scope_read

# ---------------------------------------------------------------------------
# Range / coupling code tables
# ---------------------------------------------------------------------------

# Maps voltage_range (V peak-to-peak) to device gain setting byte
RANGE_CODES: dict[float, int] = {
    0.5:  0x01,  # ±0.25 V
    1.0:  0x02,  # ±0.5 V
    2.0:  0x03,  # ±1 V
    5.0:  0x04,  # ±2.5 V
    10.0: 0x05,  # ±5 V
    25.0: 0x06,  # ±12.5 V
    50.0: 0x07,  # ±25 V
}

COUPLING_CODES: dict[str, int] = {
    "DC": 0x00,
    "AC": 0x01,
}

# ADC full-scale count (14-bit signed: 2^13)
_ADC_FULL_SCALE = 8192


class OscilloscopeChannel(OscilloscopeBase):
    """Oscilloscope driver for the Analog Discovery (14-bit, ±25 V with 10× probe).

    Two channels are multiplexed through a single :class:`~.pti.PTIController`.
    """

    def __init__(self, pti: PTIController) -> None:
        self._pti = pti
        # Per-channel configuration state
        self._num_samples: dict[int, int] = {}
        self._voltage_range: dict[int, float] = {}
        self._coupling: dict[int, str] = {}
        self._offset_raw: dict[int, int] = {}

    # ------------------------------------------------------------------
    # OscilloscopeBase implementation
    # ------------------------------------------------------------------

    def configure_channel(
        self,
        channel: int,
        voltage_range: float,
        sample_rate: float,
        num_samples: int,
    ) -> None:
        """Configure an input channel before acquisition.

        Args:
            channel: Zero-based channel index (0 or 1).
            voltage_range: Full-scale input range in volts (peak-to-peak).
                Must be one of the keys in :data:`RANGE_CODES`.
            sample_rate: Desired sample rate in Hz (stored; sent in configure cmd).
            num_samples: Number of samples to acquire per trigger.

        Raises:
            ValueError: If *voltage_range* is not a recognised range code.
        """
        if voltage_range not in RANGE_CODES:
            raise ValueError(
                f"Unsupported voltage_range {voltage_range!r} V. "
                f"Valid values: {sorted(RANGE_CODES)}"
            )

        range_code = RANGE_CODES[voltage_range]
        coupling = self._coupling.get(channel, "DC")
        coupling_code = COUPLING_CODES[coupling]
        offset_raw = self._offset_raw.get(channel, 0)

        # Persist state for later use in arm_trigger / read_samples
        self._voltage_range[channel] = voltage_range
        self._num_samples[channel] = num_samples
        self._coupling[channel] = coupling

        cmd = scope_configure(
            ch=channel,
            range_code=range_code,
            coupling_code=coupling_code,
            offset_raw=offset_raw,
        )
        self._pti.send(cmd)

    def arm_trigger(
        self,
        source: str = "none",
        level: float = 0.0,
        edge: str = "rising",
    ) -> None:
        """Arm the trigger and prepare for acquisition.

        Args:
            source: Trigger source — "none", "ch0", "ch1", or "ext".
            level: Trigger threshold in volts.
            edge: Edge polarity — "rising" or "falling".

        Raises:
            ValueError: On unrecognised *source* or *edge* value.
        """
        source_codes = {
            "none": 0x00,
            "ch0":  0x01,
            "ch1":  0x02,
            "ext":  0x03,
        }
        edge_codes = {
            "rising":  0x00,
            "falling": 0x01,
        }

        if source not in source_codes:
            raise ValueError(
                f"Unknown trigger source {source!r}. Valid: {list(source_codes)}"
            )
        if edge not in edge_codes:
            raise ValueError(
                f"Unknown edge {edge!r}. Valid: {list(edge_codes)}"
            )

        source_code = source_codes[source]
        edge_code = edge_codes[edge]

        # Derive voltage_range from the trigger source channel if applicable
        trigger_ch = {"ch0": 0, "ch1": 1}.get(source, 0)
        voltage_range = self._voltage_range.get(trigger_ch, 10.0)

        # Scale level to 14-bit signed ADC counts
        level_raw = int(level / (voltage_range / 2.0) * _ADC_FULL_SCALE)
        level_raw = max(-_ADC_FULL_SCALE, min(_ADC_FULL_SCALE - 1, level_raw))

        cmd = scope_arm(
            source=source_code,
            level_raw=level_raw,
            edge=edge_code,
            pretrig=0,
        )
        self._pti.send(cmd)

    def read_samples(self, channel: int) -> np.ndarray:
        """Block until acquisition completes and return voltage samples.

        Args:
            channel: Zero-based channel index to read.

        Returns:
            1-D float64 array of voltage samples.

        Raises:
            ValueError: If *channel* has not been configured via :meth:`configure_channel`.
            ValueError: If the device returns fewer bytes than expected.
        """
        if channel not in self._num_samples:
            raise ValueError(
                f"Channel {channel} has not been configured; call configure_channel() first."
            )
        n_samples = self._num_samples[channel]
        voltage_range = self._voltage_range.get(channel, 10.0)

        cmd = scope_read(ch=channel, n_samples=n_samples)
        raw_bytes = self._pti.send(cmd)

        expected = n_samples * 2
        if len(raw_bytes) < expected:
            raise ValueError(
                f"read_samples: expected {expected} bytes from device, got {len(raw_bytes)}"
            )

        # Parse as signed 16-bit big-endian words
        words = struct.unpack(f">{n_samples}h", raw_bytes[: n_samples * 2])

        # ADC is 14-bit: raw 16-bit words carry the 14-bit value in the upper
        # bits (or as-is if already 14-bit aligned). Shift right by 2 to get
        # the 14-bit signed value in [-8192, 8191].
        samples = np.array(words, dtype=np.int32) >> 2

        # Scale to volts: full-scale range spans ±(voltage_range / 2)
        volts = samples.astype(np.float64) / _ADC_FULL_SCALE * (voltage_range / 2.0)
        return volts
