"""Unit tests for the PTI command protocol layer (pti.py).

All tests mock FtdiTransport so no hardware is required.
"""

from __future__ import annotations

import struct
from unittest.mock import MagicMock, call

import pytest

from python.drivers.analog_discovery.pti import (
    CMD_AWG_DATA,
    CMD_AWG_ENABLE,
    CMD_DIO_DIR,
    CMD_DIO_READ,
    CMD_DIO_WRITE,
    CMD_PSU_VNEG,
    CMD_PSU_VPOS,
    CMD_SCOPE_ARM,
    CMD_SCOPE_CONFIGURE,
    CMD_SCOPE_READ,
    PTICommand,
    PTIController,
    PTIError,
    awg_data,
    awg_enable,
    dio_read,
    dio_set_dir,
    dio_write,
    psu_vneg,
    psu_vpos,
    scope_arm,
    scope_configure,
    scope_read,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_controller():
    """Return a PTIController backed by a fresh MagicMock transport."""
    transport = MagicMock()
    controller = PTIController(transport)
    return controller, transport


def ack_header(cmd_byte: int, proto: int = 0x02) -> bytes:
    """Build a minimal 8-byte ACK response header."""
    return bytes([0x00, 0x00, 0x00, 0x00, 0x00, proto, cmd_byte, 0x00])


# ---------------------------------------------------------------------------
# _pack() tests
# ---------------------------------------------------------------------------

class TestPack:
    def test_header_format_with_payload(self):
        """Header bytes are assembled correctly when payload is non-empty."""
        ctrl, _ = make_controller()
        payload = bytes([0xAA, 0xBB, 0xCC])
        cmd = PTICommand(cmd=0x01, port=2, payload=payload)

        packed = ctrl._pack(cmd)

        # Offsets 0-3: zero padding
        assert packed[:4] == bytes([0x00, 0x00, 0x00, 0x00])
        # Offset 4: payload length - 1
        assert packed[4] == len(payload) - 1
        # Offset 5: protocol type
        assert packed[5] == 0x02
        # Offset 6: command byte
        assert packed[6] == 0x01
        # Offset 7: port
        assert packed[7] == 2
        # Offsets 8+: payload
        assert packed[8:] == payload

    def test_header_length_byte_with_empty_payload(self):
        """Length byte is 0 (not 0xFF) when payload is empty."""
        ctrl, _ = make_controller()
        cmd = PTICommand(cmd=0x03, port=0, payload=b"")

        packed = ctrl._pack(cmd)

        assert packed[4] == 0, (
            "Expected length byte == 0 for empty payload, got "
            f"0x{packed[4]:02X}"
        )
        assert len(packed) == 8

    def test_single_byte_payload_length_field(self):
        """A one-byte payload → length byte == 0 (1 - 1 = 0)."""
        ctrl, _ = make_controller()
        cmd = PTICommand(cmd=0x09, port=1, payload=bytes([0x01]))
        packed = ctrl._pack(cmd)
        assert packed[4] == 0

    def test_multibyte_payload_length_field(self):
        """A five-byte payload → length byte == 4 (5 - 1)."""
        ctrl, _ = make_controller()
        payload = bytes(5)
        cmd = PTICommand(cmd=0x08, port=0, payload=payload)
        packed = ctrl._pack(cmd)
        assert packed[4] == 4

    def test_total_frame_length(self):
        """Packed frame length equals 8 + len(payload)."""
        ctrl, _ = make_controller()
        for n in (0, 1, 5, 16):
            cmd = PTICommand(cmd=0x01, port=0, payload=bytes(n))
            assert len(ctrl._pack(cmd)) == 8 + n


# ---------------------------------------------------------------------------
# send() tests
# ---------------------------------------------------------------------------

class TestSend:
    def test_send_calls_write_with_packed_bytes(self):
        """send() passes the packed frame to transport.write()."""
        ctrl, transport = make_controller()
        transport.read.return_value = ack_header(CMD_SCOPE_ARM)

        cmd = PTICommand(cmd=CMD_SCOPE_ARM, port=0, payload=b"\x00\x01\x02\x03\x04\x05")
        ctrl.send(cmd)

        expected_packed = ctrl._pack(cmd)
        transport.write.assert_called_once_with(expected_packed)

    def test_send_reads_8_bytes_for_ack_only_command(self):
        """For response_size=0, send() reads exactly 8 bytes."""
        ctrl, transport = make_controller()
        transport.read.return_value = ack_header(CMD_AWG_ENABLE)

        cmd = PTICommand(cmd=CMD_AWG_ENABLE, port=0, payload=b"\x01")
        ctrl.send(cmd)

        transport.read.assert_called_once_with(8)

    def test_send_reads_header_plus_response_payload(self):
        """For response_size=N, send() reads 8+N bytes."""
        ctrl, transport = make_controller()
        n_samples = 10
        response_payload = bytes(n_samples * 2)
        transport.read.return_value = ack_header(CMD_SCOPE_READ) + response_payload

        cmd = PTICommand(cmd=CMD_SCOPE_READ, port=0, payload=b"", response_size=n_samples * 2)
        result = ctrl.send(cmd)

        transport.read.assert_called_once_with(8 + n_samples * 2)
        assert result == response_payload

    def test_send_returns_empty_bytes_for_ack_command(self):
        """send() returns empty bytes when the response has no payload."""
        ctrl, transport = make_controller()
        transport.read.return_value = ack_header(CMD_PSU_VPOS)

        cmd = PTICommand(cmd=CMD_PSU_VPOS, port=0, payload=b"\x04\x00")
        result = ctrl.send(cmd)

        assert result == b""

    def test_send_raises_pti_error_on_bad_response(self):
        """send() propagates PTIError raised by _unpack_response()."""
        ctrl, transport = make_controller()
        # Wrong protocol type in response
        bad_header = bytes([0, 0, 0, 0, 0, 0xFF, CMD_PSU_VPOS, 0])
        transport.read.return_value = bad_header

        cmd = PTICommand(cmd=CMD_PSU_VPOS, port=0, payload=b"\x04\x00")
        with pytest.raises(PTIError):
            ctrl.send(cmd)


# ---------------------------------------------------------------------------
# _unpack_response() tests
# ---------------------------------------------------------------------------

class TestUnpackResponse:
    def test_raises_on_wrong_protocol_type(self):
        ctrl, _ = make_controller()
        cmd = PTICommand(cmd=0x01, port=0)
        bad_response = bytes([0, 0, 0, 0, 0, 0xFF, 0x01, 0])  # proto=0xFF

        with pytest.raises(PTIError, match="Protocol type mismatch"):
            ctrl._unpack_response(cmd, bad_response)

    def test_raises_on_wrong_command_byte(self):
        ctrl, _ = make_controller()
        cmd = PTICommand(cmd=0x01, port=0)
        bad_response = bytes([0, 0, 0, 0, 0, 0x02, 0x99, 0])  # cmd=0x99

        with pytest.raises(PTIError, match="Command byte mismatch"):
            ctrl._unpack_response(cmd, bad_response)

    def test_raises_on_short_response(self):
        ctrl, _ = make_controller()
        cmd = PTICommand(cmd=0x01, port=0)

        with pytest.raises(PTIError, match="too short"):
            ctrl._unpack_response(cmd, bytes(4))

    def test_returns_payload_on_valid_response(self):
        ctrl, _ = make_controller()
        cmd = PTICommand(cmd=CMD_DIO_READ, port=0)
        payload = bytes([0xAB, 0xCD])
        raw = ack_header(CMD_DIO_READ) + payload

        result = ctrl._unpack_response(cmd, raw)
        assert result == payload

    def test_returns_empty_bytes_on_ack_only_response(self):
        ctrl, _ = make_controller()
        cmd = PTICommand(cmd=CMD_AWG_ENABLE, port=0)
        raw = ack_header(CMD_AWG_ENABLE)  # no payload

        result = ctrl._unpack_response(cmd, raw)
        assert result == b""


# ---------------------------------------------------------------------------
# Convenience constructor tests
# ---------------------------------------------------------------------------

class TestConvenienceConstructors:
    def test_scope_configure(self):
        cmd = scope_configure(ch=1, range_code=2, coupling_code=0, offset_raw=-256)
        assert cmd.cmd == CMD_SCOPE_CONFIGURE
        assert cmd.port == 1
        assert cmd.response_size == 0
        # Payload: ch(1B) range(1B) coupling(1B) offset(2B signed big-endian)
        assert cmd.payload == struct.pack(">BBBh", 1, 2, 0, -256)

    def test_scope_arm(self):
        cmd = scope_arm(source=0, level_raw=512, edge=1, pretrig=100)
        assert cmd.cmd == CMD_SCOPE_ARM
        assert cmd.port == 0
        assert cmd.payload == struct.pack(">BhBH", 0, 512, 1, 100)

    def test_scope_read(self):
        cmd = scope_read(ch=0, n_samples=500)
        assert cmd.cmd == CMD_SCOPE_READ
        assert cmd.port == 0
        assert cmd.payload == b""
        assert cmd.response_size == 1000  # 500 * 2

    def test_awg_data(self):
        dac_words = struct.pack(">HHH", 0x0000, 0x7FFF, 0x8000)
        cmd = awg_data(ch=0, dac_words=dac_words)
        assert cmd.cmd == CMD_AWG_DATA
        assert cmd.port == 0
        assert cmd.payload == dac_words

    def test_awg_enable_true(self):
        cmd = awg_enable(ch=1, enabled=True)
        assert cmd.cmd == CMD_AWG_ENABLE
        assert cmd.port == 1
        assert cmd.payload == bytes([0x01])

    def test_awg_enable_false(self):
        cmd = awg_enable(ch=0, enabled=False)
        assert cmd.payload == bytes([0x00])

    def test_psu_vpos(self):
        cmd = psu_vpos(dac_code=0x1234)
        assert cmd.cmd == CMD_PSU_VPOS
        assert cmd.port == 0
        assert cmd.payload == struct.pack(">H", 0x1234)

    def test_psu_vneg(self):
        cmd = psu_vneg(dac_code=0xABCD)
        assert cmd.cmd == CMD_PSU_VNEG
        assert cmd.payload == struct.pack(">H", 0xABCD)

    def test_dio_set_dir(self):
        cmd = dio_set_dir(mask=0xFF00)
        assert cmd.cmd == CMD_DIO_DIR
        assert cmd.payload == struct.pack(">H", 0xFF00)

    def test_dio_write(self):
        cmd = dio_write(value=0x00FF)
        assert cmd.cmd == CMD_DIO_WRITE
        assert cmd.payload == struct.pack(">H", 0x00FF)

    def test_dio_read(self):
        cmd = dio_read()
        assert cmd.cmd == CMD_DIO_READ
        assert cmd.payload == b""
        assert cmd.response_size == 2
