"""Arbitrary Waveform Generator instrument module for the Digilent Analog Discovery.

Implements :class:`~python.drivers.base.AWGBase` using PTI commands.
"""

from __future__ import annotations

import struct

import numpy as np

from ..base import AWGBase
from .pti import PTIController, awg_data, awg_enable

# DAC is 14-bit unsigned: 0 → -5 V, 8192 → 0 V, 16383 → +5 V
_DAC_MAX = 16383
_DAC_MID = 8192  # midpoint ≈ 0 V  (exact midpoint of 0–16383 is 8191.5)

# PTI payload cap is 256 bytes → max 128 DAC words per chunk
_MAX_WORDS_PER_CHUNK = 128


class WaveformGenerator(AWGBase):
    """AWG driver for the Analog Discovery (14-bit, ±5 V output range).

    Waveforms are given as normalised float64 in [-1.0, 1.0] and are
    mapped to the 14-bit unsigned DAC codes before upload.
    """

    def __init__(self, pti: PTIController) -> None:
        self._pti = pti
        # Per-channel state for amplitude/offset scaling
        self._amplitude: dict[int, float] = {}  # peak volts (≤5.0)
        self._offset: dict[int, float] = {}      # DC offset volts
        # Last uploaded waveform (normalised), so set_amplitude can re-upload
        self._last_waveform: dict[int, np.ndarray] = {}
        self._last_sample_rate: dict[int, float] = {}

    # ------------------------------------------------------------------
    # AWGBase implementation
    # ------------------------------------------------------------------

    def set_waveform(self, channel: int, waveform: np.ndarray, sample_rate: float) -> None:
        """Upload an arbitrary waveform to a channel.

        Args:
            channel: Zero-based AWG channel index.
            waveform: Normalised float64 array in [-1.0, 1.0]; clipped if outside.
            sample_rate: Playback sample rate in Hz.
        """
        # Persist for potential re-upload via set_amplitude
        self._last_waveform[channel] = np.array(waveform, dtype=np.float64)
        self._last_sample_rate[channel] = sample_rate

        self._upload_waveform(channel, waveform)

    def enable(self, channel: int, enabled: bool) -> None:
        """Enable or disable an AWG output channel.

        Args:
            channel: Zero-based AWG channel index.
            enabled: True to enable the output, False to disable.
        """
        cmd = awg_enable(ch=channel, enabled=enabled)
        self._pti.send(cmd)

    def set_amplitude(self, channel: int, amplitude: float, offset: float = 0.0) -> None:
        """Set the output amplitude and DC offset, re-uploading if waveform exists.

        Args:
            channel: Zero-based AWG channel index.
            amplitude: Peak amplitude in volts.
            offset: DC offset in volts (default 0.0).
        """
        self._amplitude[channel] = amplitude
        self._offset[channel] = offset

        # Re-upload with new scaling if a waveform was previously set
        if channel in self._last_waveform:
            self._upload_waveform(channel, self._last_waveform[channel])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _upload_waveform(self, channel: int, waveform: np.ndarray) -> None:
        """Convert *waveform* to DAC codes and send to device in chunks."""
        # Clip normalised input to [-1, 1]
        clipped = np.clip(waveform, -1.0, 1.0)

        # Apply amplitude / offset scaling if configured
        amplitude = self._amplitude.get(channel, 5.0)  # default: full ±5 V scale
        offset_v = self._offset.get(channel, 0.0)

        # Scale: normalised [-1,1] → volts → DAC code
        # volts = clipped * amplitude + offset_v
        # dac = (volts + 5.0) / 10.0 * 16383  (mapping -5→0, +5→16383)
        volts = clipped * amplitude + offset_v
        dac_float = (volts + 5.0) / 10.0 * _DAC_MAX
        dac_codes = np.clip(np.round(dac_float), 0, _DAC_MAX).astype(np.uint16)

        # Send in chunks to respect 256-byte PTI payload limit
        for start in range(0, len(dac_codes), _MAX_WORDS_PER_CHUNK):
            chunk = dac_codes[start: start + _MAX_WORDS_PER_CHUNK]
            # Pack as big-endian unsigned 16-bit words
            packed = struct.pack(f">{len(chunk)}H", *chunk)
            cmd = awg_data(ch=channel, dac_words=packed)
            self._pti.send(cmd)
