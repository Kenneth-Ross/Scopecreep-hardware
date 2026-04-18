"""Unit tests for the four Analog Discovery instrument modules.

All PTI I/O is intercepted via a lightweight mock that records every
:meth:`PTIController.send` call and returns a configurable response.
"""

from __future__ import annotations

import struct
from unittest.mock import MagicMock, call, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pti(response: bytes = b"") -> MagicMock:
    """Return a mock PTIController whose send() returns *response*."""
    pti = MagicMock()
    pti.send.return_value = response
    return pti


# ---------------------------------------------------------------------------
# Oscilloscope tests
# ---------------------------------------------------------------------------

class TestOscilloscopeChannel:
    """Tests for oscilloscope.OscilloscopeChannel."""

    def _make(self, response: bytes = b""):
        from python.drivers.analog_discovery.oscilloscope import OscilloscopeChannel
        pti = _make_pti(response)
        return OscilloscopeChannel(pti), pti

    def test_configure_channel_sends_pti_command(self):
        """configure_channel must call pti.send() exactly once."""
        osc, pti = self._make()
        osc.configure_channel(channel=0, voltage_range=10.0, sample_rate=1e6, num_samples=100)
        assert pti.send.call_count == 1

    def test_configure_channel_uses_correct_range_code(self):
        """range_code 0x05 should appear in the payload for voltage_range=10.0."""
        from python.drivers.analog_discovery.pti import CMD_SCOPE_CONFIGURE
        osc, pti = self._make()
        osc.configure_channel(channel=0, voltage_range=10.0, sample_rate=1e6, num_samples=100)
        cmd = pti.send.call_args[0][0]
        assert cmd.cmd == CMD_SCOPE_CONFIGURE
        # payload: ch(1) range(1) coupling(1) offset_raw(2) → range_code is byte [1]
        assert cmd.payload[1] == 0x05  # RANGE_CODES[10.0]

    def test_configure_channel_raises_on_unknown_voltage_range(self):
        """ValueError on voltage_range not in RANGE_CODES."""
        osc, _ = self._make()
        with pytest.raises(ValueError, match="Unsupported voltage_range"):
            osc.configure_channel(channel=0, voltage_range=3.0, sample_rate=1e6, num_samples=10)

    def test_read_samples_returns_float64_ndarray_correct_length(self):
        """read_samples must return a float64 array of length num_samples."""
        n = 64
        # Build synthetic 16-bit big-endian signed response
        raw_words = [0] * n
        raw = struct.pack(f">{n}h", *raw_words)
        osc, _ = self._make(response=raw)
        osc.configure_channel(channel=0, voltage_range=10.0, sample_rate=1e6, num_samples=n)
        result = osc.read_samples(channel=0)
        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float64
        assert len(result) == n

    def test_read_samples_adc_scaling_midscale(self):
        """ADC raw 8192 (left-shifted as 16-bit) → +0.5 × voltage_range/2 volts.

        The device gives 14-bit values packed in 16-bit words.  A raw 14-bit
        count of 8192 is stored as 8192<<2 = 32768 in the 16-bit word (signed
        → -32768 due to two's complement).

        Wait — 8192 as a 14-bit signed value is actually out of range (14-bit
        signed max is 8191).  Per the spec: ADC range ±8192 counts, so 8192
        represents full-scale positive.  We test with raw16 = 8192*4 = 32768,
        which as a signed int16 is -32768.  After >>2 we get -8192.

        Instead test with raw14 = 4096 (half full-scale):
        raw16 = 4096 << 2 = 16384, signed int16 = 16384.
        After >> 2 = 4096.
        volts = 4096 / 8192 * (10.0/2) = 0.5 * 5.0 = 2.5 V.
        """
        n = 1
        raw14 = 4096
        raw16 = raw14 << 2  # 16384 — fits in signed int16
        raw = struct.pack(f">{n}h", raw16)
        osc, _ = self._make(response=raw)
        osc.configure_channel(channel=0, voltage_range=10.0, sample_rate=1e6, num_samples=n)
        result = osc.read_samples(channel=0)
        expected = 4096 / 8192.0 * (10.0 / 2.0)  # 2.5 V
        assert abs(result[0] - expected) < 1e-9

    def test_arm_trigger_sends_pti_command(self):
        """arm_trigger must call pti.send() once."""
        from python.drivers.analog_discovery.pti import CMD_SCOPE_ARM
        osc, pti = self._make()
        osc.configure_channel(channel=0, voltage_range=10.0, sample_rate=1e6, num_samples=100)
        osc.arm_trigger(source="ch0", level=1.0, edge="rising")
        # configure + arm = 2 calls
        assert pti.send.call_count == 2
        arm_cmd = pti.send.call_args[0][0]
        assert arm_cmd.cmd == CMD_SCOPE_ARM

    def test_arm_trigger_raises_on_unknown_source(self):
        osc, _ = self._make()
        with pytest.raises(ValueError, match="Unknown trigger source"):
            osc.arm_trigger(source="bad", level=0.0, edge="rising")

    def test_arm_trigger_raises_on_unknown_edge(self):
        osc, _ = self._make()
        with pytest.raises(ValueError, match="Unknown edge"):
            osc.arm_trigger(source="none", level=0.0, edge="bad")


# ---------------------------------------------------------------------------
# AWG tests
# ---------------------------------------------------------------------------

class TestWaveformGenerator:
    """Tests for awg.WaveformGenerator."""

    def _make(self):
        from python.drivers.analog_discovery.awg import WaveformGenerator
        pti = _make_pti()
        return WaveformGenerator(pti), pti

    def test_set_waveform_sends_pti_command(self):
        awg, pti = self._make()
        waveform = np.zeros(10)
        awg.set_waveform(channel=0, waveform=waveform, sample_rate=1e6)
        assert pti.send.call_count >= 1

    def test_set_waveform_clips_to_plus_minus_one(self):
        """Values outside [-1, 1] must be clipped before DAC encoding."""
        from python.drivers.analog_discovery.pti import CMD_AWG_DATA
        awg, pti = self._make()
        waveform = np.array([2.0, -3.0])  # out of range
        awg.set_waveform(channel=0, waveform=waveform, sample_rate=1e6)
        cmd = pti.send.call_args[0][0]
        assert cmd.cmd == CMD_AWG_DATA
        words = struct.unpack(f">2H", cmd.payload)
        # +1.0 → DAC ~16383, -1.0 → DAC 0
        assert words[0] == 16383
        assert words[1] == 0

    def test_set_waveform_midpoint_zero(self):
        """waveform=0.0 with default amplitude should give DAC code ≈ 8192."""
        awg, pti = self._make()
        waveform = np.array([0.0])
        awg.set_waveform(channel=0, waveform=waveform, sample_rate=1e6)
        cmd = pti.send.call_args[0][0]
        (dac_code,) = struct.unpack(">H", cmd.payload)
        # Exact: (0.0*5.0 + 0.0 + 5.0) / 10.0 * 16383 = 8191.5 → rounds to 8192
        assert dac_code in (8191, 8192)

    def test_set_waveform_packs_unsigned_16bit_words(self):
        """DAC words must be unsigned 16-bit big-endian."""
        awg, pti = self._make()
        waveform = np.array([1.0])
        awg.set_waveform(channel=0, waveform=waveform, sample_rate=1e6)
        cmd = pti.send.call_args[0][0]
        assert len(cmd.payload) == 2
        (val,) = struct.unpack(">H", cmd.payload)
        assert val == 16383

    def test_enable_true_sends_correct_payload(self):
        """enable(ch, True) must send AWG_ENABLE with byte 0x01."""
        from python.drivers.analog_discovery.pti import CMD_AWG_ENABLE
        awg, pti = self._make()
        awg.enable(channel=1, enabled=True)
        cmd = pti.send.call_args[0][0]
        assert cmd.cmd == CMD_AWG_ENABLE
        assert cmd.payload == bytes([0x01])
        assert cmd.port == 1

    def test_enable_false_sends_correct_payload(self):
        """enable(ch, False) must send AWG_ENABLE with byte 0x00."""
        from python.drivers.analog_discovery.pti import CMD_AWG_ENABLE
        awg, pti = self._make()
        awg.enable(channel=0, enabled=False)
        cmd = pti.send.call_args[0][0]
        assert cmd.cmd == CMD_AWG_ENABLE
        assert cmd.payload == bytes([0x00])

    def test_set_amplitude_stores_values(self):
        """set_amplitude persists amplitude and offset."""
        awg, pti = self._make()
        # No waveform yet — should not crash
        awg.set_amplitude(channel=0, amplitude=2.5, offset=1.0)
        assert awg._amplitude[0] == 2.5
        assert awg._offset[0] == 1.0

    def test_set_amplitude_reuploads_waveform(self):
        """After set_amplitude, if a waveform exists it should be re-uploaded."""
        awg, pti = self._make()
        waveform = np.array([0.0])
        awg.set_waveform(channel=0, waveform=waveform, sample_rate=1e6)
        first_count = pti.send.call_count
        awg.set_amplitude(channel=0, amplitude=2.5, offset=0.0)
        assert pti.send.call_count > first_count

    def test_large_waveform_chunked(self):
        """Waveforms >128 samples must be split into multiple PTI sends."""
        awg, pti = self._make()
        waveform = np.zeros(300)
        awg.set_waveform(channel=0, waveform=waveform, sample_rate=1e6)
        # 300 samples / 128 per chunk = 3 sends (128 + 128 + 44)
        assert pti.send.call_count == 3


# ---------------------------------------------------------------------------
# PSU tests
# ---------------------------------------------------------------------------

class TestPowerSupply:
    """Tests for power.PowerSupply."""

    def _make(self):
        from python.drivers.analog_discovery.power import PowerSupply
        pti = _make_pti()
        return PowerSupply(pti), pti

    def test_set_voltage_vpos_correct_dac_code(self):
        """set_voltage(0, 3.3) → V+ DAC code ≈ 2710."""
        from python.drivers.analog_discovery.pti import CMD_PSU_VPOS
        psu, pti = self._make()
        psu.set_voltage(channel=0, voltage=3.3)
        cmd = pti.send.call_args[0][0]
        assert cmd.cmd == CMD_PSU_VPOS
        (code,) = struct.unpack(">H", cmd.payload)
        expected = int(3.3 / 5.0 * 4095)  # 2706
        assert abs(code - expected) <= 1

    def test_set_voltage_vneg_correct_dac_code(self):
        """set_voltage(1, -2.5) → V- DAC code ≈ 2047."""
        from python.drivers.analog_discovery.pti import CMD_PSU_VNEG
        psu, pti = self._make()
        psu.set_voltage(channel=1, voltage=-2.5)
        cmd = pti.send.call_args[0][0]
        assert cmd.cmd == CMD_PSU_VNEG
        (code,) = struct.unpack(">H", cmd.payload)
        expected = int(2.5 / 5.0 * 4095)  # 2047
        assert abs(code - expected) <= 1

    def test_set_voltage_vpos_out_of_range_raises(self):
        """Voltage above +5 V on V+ must raise ValueError."""
        psu, _ = self._make()
        with pytest.raises(ValueError, match="out of range"):
            psu.set_voltage(channel=0, voltage=6.0)

    def test_set_voltage_vneg_positive_raises(self):
        """Positive voltage on V− channel must raise ValueError."""
        psu, _ = self._make()
        with pytest.raises(ValueError, match="out of range"):
            psu.set_voltage(channel=1, voltage=1.0)

    def test_set_voltage_vpos_negative_raises(self):
        """Negative voltage on V+ channel must raise ValueError."""
        psu, _ = self._make()
        with pytest.raises(ValueError, match="out of range"):
            psu.set_voltage(channel=0, voltage=-1.0)

    def test_set_voltage_vneg_below_minus5_raises(self):
        """Voltage below −5 V on V- must raise ValueError."""
        psu, _ = self._make()
        with pytest.raises(ValueError, match="out of range"):
            psu.set_voltage(channel=1, voltage=-6.0)

    def test_set_voltage_unknown_channel_raises(self):
        psu, _ = self._make()
        with pytest.raises(ValueError, match="Unknown PSU channel"):
            psu.set_voltage(channel=2, voltage=1.0)

    def test_enable_false_sends_zero_voltage(self):
        """Disabling a rail must send DAC code 0."""
        from python.drivers.analog_discovery.pti import CMD_PSU_VPOS
        psu, pti = self._make()
        psu.set_voltage(channel=0, voltage=3.3)
        pti.reset_mock()
        psu.enable(channel=0, enabled=False)
        cmd = pti.send.call_args[0][0]
        assert cmd.cmd == CMD_PSU_VPOS
        (code,) = struct.unpack(">H", cmd.payload)
        assert code == 0

    def test_enable_true_restores_voltage(self):
        """Re-enabling a rail must restore the last programmed voltage."""
        from python.drivers.analog_discovery.pti import CMD_PSU_VPOS
        psu, pti = self._make()
        psu.set_voltage(channel=0, voltage=3.3)
        psu.enable(channel=0, enabled=False)
        pti.reset_mock()
        psu.enable(channel=0, enabled=True)
        cmd = pti.send.call_args[0][0]
        (code,) = struct.unpack(">H", cmd.payload)
        expected = int(3.3 / 5.0 * 4095)
        assert abs(code - expected) <= 1


# ---------------------------------------------------------------------------
# DIO tests
# ---------------------------------------------------------------------------

class TestDigitalIO:
    """Tests for digital.DigitalIO."""

    def _make(self, read_response: bytes = b"\x00\x00"):
        from python.drivers.analog_discovery.digital import DigitalIO
        pti = _make_pti(response=read_response)
        return DigitalIO(pti), pti

    def test_set_direction_sends_dir_command(self):
        """set_direction must send CMD_DIO_DIR with correct merged mask."""
        from python.drivers.analog_discovery.pti import CMD_DIO_DIR
        dio, pti = self._make()
        dio.set_direction(pin_mask=0x00FF, output_mask=0x00FF)
        cmd = pti.send.call_args[0][0]
        assert cmd.cmd == CMD_DIO_DIR
        (mask,) = struct.unpack(">H", cmd.payload)
        assert mask == 0x00FF

    def test_set_direction_only_changes_masked_pins(self):
        """Pins outside pin_mask must not be altered."""
        from python.drivers.analog_discovery.pti import CMD_DIO_DIR
        dio, pti = self._make()
        # Set upper byte as outputs first
        dio.set_direction(pin_mask=0xFF00, output_mask=0xFF00)
        # Now set lower nibble as outputs, leaving upper byte unchanged
        dio.set_direction(pin_mask=0x000F, output_mask=0x000F)
        cmd = pti.send.call_args[0][0]
        (mask,) = struct.unpack(">H", cmd.payload)
        assert mask == 0xFF0F

    def test_set_direction_clears_bits_correctly(self):
        """Bits set to 0 in output_mask (within pin_mask) must become inputs."""
        dio, pti = self._make()
        dio.set_direction(pin_mask=0xFFFF, output_mask=0xFFFF)  # all outputs
        dio.set_direction(pin_mask=0x00FF, output_mask=0x0000)  # lower byte → input
        cmd = pti.send.call_args[0][0]
        (mask,) = struct.unpack(">H", cmd.payload)
        assert mask == 0xFF00

    def test_write_merges_into_output_register(self):
        """write must merge value_mask into output register."""
        from python.drivers.analog_discovery.pti import CMD_DIO_WRITE
        dio, pti = self._make()
        dio.write(pin_mask=0x00FF, value_mask=0x00AA)
        cmd = pti.send.call_args[0][0]
        assert cmd.cmd == CMD_DIO_WRITE
        (val,) = struct.unpack(">H", cmd.payload)
        assert val == 0x00AA

    def test_write_only_changes_masked_pins(self):
        """Pins outside pin_mask must retain their current output value."""
        dio, pti = self._make()
        dio.write(pin_mask=0xFF00, value_mask=0xFF00)  # upper byte high
        dio.write(pin_mask=0x00FF, value_mask=0x0055)  # lower byte
        cmd = pti.send.call_args[0][0]
        (val,) = struct.unpack(">H", cmd.payload)
        assert val == 0xFF55

    def test_read_masks_response(self):
        """read must return pin state masked by pin_mask."""
        response = struct.pack(">H", 0xABCD)
        dio, pti = self._make(read_response=response)
        result = dio.read(pin_mask=0x00FF)
        assert result == (0xABCD & 0x00FF)  # 0x00CD

    def test_read_sends_dio_read_command(self):
        """read must send a CMD_DIO_READ command."""
        from python.drivers.analog_discovery.pti import CMD_DIO_READ
        response = struct.pack(">H", 0x0000)
        dio, pti = self._make(read_response=response)
        dio.read(pin_mask=0xFFFF)
        cmd = pti.send.call_args[0][0]
        assert cmd.cmd == CMD_DIO_READ

    def test_read_all_pins(self):
        """read with mask=0xFFFF returns full 16-bit pin state."""
        response = struct.pack(">H", 0x1234)
        dio, pti = self._make(read_response=response)
        result = dio.read(pin_mask=0xFFFF)
        assert result == 0x1234
