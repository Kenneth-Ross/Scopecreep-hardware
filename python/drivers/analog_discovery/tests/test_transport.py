"""Unit tests for FtdiTransport.

Hardware is NOT required.  All tests either:
  - verify import/instantiation behaviour (no I/O), or
  - use unittest.mock to simulate pyftdi responses.
"""

import math
import types
import sys
import unittest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helper: build a minimal pyftdi.ftdi stub so we can import transport.py even
# when pyftdi is not installed (e.g. in a bare CI environment).
# ---------------------------------------------------------------------------
def _ensure_pyftdi_stub() -> None:
    """Insert a minimal pyftdi stub into sys.modules if pyftdi is absent."""
    if "pyftdi" in sys.modules:
        return  # real library present — use it

    ftdi_mod = types.ModuleType("pyftdi.ftdi")

    class _FtdiFake:
        # MPSSE command bytes (values match the real Ftdi constants)
        SET_BITS_HIGH = 0x82
        GET_BITS_HIGH = 0x83
        SET_BITS_LOW = 0x80
        GET_BITS_LOW = 0x81
        SET_TCK_DIVISOR = 0x86
        SEND_IMMEDIATE = 0x87
        LOOPBACK_END = 0x85
        DISABLE_CLK_DIV5 = 0x8A
        RW_BYTES_PVE_NVE_LSB = 0x39
        RW_BITS_PVE_NVE_LSB = 0x3B
        RW_BITS_TMS_PVE_NVE = 0x3F
        WRITE_BYTES_NVE_LSB = 0x19
        READ_BYTES_PVE_LSB = 0x28

        def open_mpsse_from_url(self, *a, **kw):
            raise IOError("No FTDI device (stub)")

        @property
        def is_connected(self):
            return False

    ftdi_mod.Ftdi = _FtdiFake

    pyftdi_pkg = types.ModuleType("pyftdi")
    sys.modules.setdefault("pyftdi", pyftdi_pkg)
    sys.modules["pyftdi.ftdi"] = ftdi_mod


_ensure_pyftdi_stub()

# Now we can safely import
from drivers.analog_discovery.transport import (  # noqa: E402
    FtdiTransport,
    TransportError,
    _ACBUS_DIRECTION,
    _ACBUS_IDLE,
    _ADBUS_DIRECTION,
    _ADBUS_IDLE,
    _DONE_BIT,
    _INIT_B_BIT,
    _PROGRAM_B_BIT,
)


# ---------------------------------------------------------------------------
# Instantiation tests (no hardware)
# ---------------------------------------------------------------------------

class TestFtdiTransportInstantiation(unittest.TestCase):
    def test_can_be_instantiated(self):
        t = FtdiTransport()
        self.assertIsInstance(t, FtdiTransport)

    def test_initially_closed(self):
        t = FtdiTransport()
        self.assertIsNone(t._ftdi)

    def test_default_url_attribute(self):
        self.assertEqual(FtdiTransport.DEFAULT_URL, "ftdi://0x0403:0x6014/1")


# ---------------------------------------------------------------------------
# open() raises TransportError when no device present
# ---------------------------------------------------------------------------

class TestOpenRaisesOnMissingDevice(unittest.TestCase):
    # Use explicit patch so the test is isolated from whichever Ftdi stub or
    # real library ends up in sys.modules depending on collection order.
    def _make_failing_ftdi(self):
        """Return a mock Ftdi class whose open_mpsse_from_url raises IOError."""
        mock_instance = MagicMock()
        mock_instance.open_mpsse_from_url.side_effect = IOError("No FTDI device")
        return MagicMock(return_value=mock_instance)

    def test_open_raises_transport_error_no_device(self):
        t = FtdiTransport()
        with patch("drivers.analog_discovery.transport.Ftdi", self._make_failing_ftdi()):
            with self.assertRaises(TransportError) as ctx:
                t.open()
        self.assertIn("Failed to open FTDI device", str(ctx.exception))

    def test_open_custom_url_in_error_message(self):
        t = FtdiTransport()
        url = "ftdi://0x0403:0x6014/2"
        with patch("drivers.analog_discovery.transport.Ftdi", self._make_failing_ftdi()):
            with self.assertRaises(TransportError) as ctx:
                t.open(url)
        self.assertIn(url, str(ctx.exception))


# ---------------------------------------------------------------------------
# _require_open guard
# ---------------------------------------------------------------------------

class TestRequireOpenGuard(unittest.TestCase):
    def _make_closed(self) -> FtdiTransport:
        t = FtdiTransport()
        t._ftdi = None
        return t

    def test_gpio_helpers_require_open(self):
        t = self._make_closed()
        for method in (
            t.assert_program_b,
            t.release_program_b,
            t.read_init_b,
            t.read_done,
        ):
            with self.subTest(method=method.__name__):
                with self.assertRaises(TransportError):
                    method()

    def test_jtag_shift_requires_open(self):
        t = self._make_closed()
        with self.assertRaises(TransportError):
            t.jtag_shift(b"\x00", 8)

    def test_write_requires_open(self):
        t = self._make_closed()
        with self.assertRaises(TransportError):
            t.write(b"\x01")

    def test_read_requires_open(self):
        t = self._make_closed()
        with self.assertRaises(TransportError):
            t.read(4)


# ---------------------------------------------------------------------------
# Mocked FT232H — tests that exercise logic without hardware
# ---------------------------------------------------------------------------

def _make_mock_ftdi(acbus_value: int = 0xFF) -> MagicMock:
    """Return a MagicMock that looks like a connected Ftdi instance."""
    mock = MagicMock()
    mock.is_connected = True
    # read_data_bytes for ACBUS read returns acbus_value
    mock.read_data_bytes.return_value = bytearray([acbus_value])
    return mock


def _transport_with_mock(acbus_value: int = 0xFF) -> tuple[FtdiTransport, MagicMock]:
    t = FtdiTransport()
    mock_ftdi = _make_mock_ftdi(acbus_value)
    t._ftdi = mock_ftdi
    t._acbus_value = _ACBUS_IDLE
    return t, mock_ftdi


class TestProgramBControl(unittest.TestCase):
    def test_assert_program_b_drives_low(self):
        t, mock = _transport_with_mock()
        t.assert_program_b()
        # Last SET_BITS_HIGH call must have PROGRAM_B bit clear (bit 0 = 0)
        calls = mock.write_data.call_args_list
        last_cmd = bytes(calls[-1][0][0])
        self.assertEqual(last_cmd[0], 0x82)  # SET_BITS_HIGH
        self.assertEqual(last_cmd[1] & (1 << _PROGRAM_B_BIT), 0)

    def test_release_program_b_drives_high(self):
        t, mock = _transport_with_mock()
        # First assert, then release
        t.assert_program_b()
        t.release_program_b()
        calls = mock.write_data.call_args_list
        last_cmd = bytes(calls[-1][0][0])
        self.assertEqual(last_cmd[0], 0x82)  # SET_BITS_HIGH
        self.assertNotEqual(last_cmd[1] & (1 << _PROGRAM_B_BIT), 0)

    def test_acbus_direction_byte_in_set_command(self):
        t, mock = _transport_with_mock()
        t.assert_program_b()
        last_cmd = bytes(mock.write_data.call_args_list[-1][0][0])
        # Direction byte must match expected mask
        self.assertEqual(last_cmd[2], _ACBUS_DIRECTION)


class TestReadInitBAndDone(unittest.TestCase):
    def test_read_init_b_high(self):
        acbus = 1 << _INIT_B_BIT
        t, mock = _transport_with_mock(acbus_value=acbus)
        mock.read_data_bytes.return_value = bytearray([acbus])
        self.assertTrue(t.read_init_b())

    def test_read_init_b_low(self):
        t, mock = _transport_with_mock(acbus_value=0x00)
        mock.read_data_bytes.return_value = bytearray([0x00])
        self.assertFalse(t.read_init_b())

    def test_read_done_high(self):
        acbus = 1 << _DONE_BIT
        t, mock = _transport_with_mock(acbus_value=acbus)
        mock.read_data_bytes.return_value = bytearray([acbus])
        self.assertTrue(t.read_done())

    def test_read_done_low(self):
        t, mock = _transport_with_mock(acbus_value=0x00)
        mock.read_data_bytes.return_value = bytearray([0x00])
        self.assertFalse(t.read_done())

    def test_read_init_b_issues_get_bits_high(self):
        t, mock = _transport_with_mock(acbus_value=0xFF)
        mock.read_data_bytes.return_value = bytearray([0xFF])
        t.read_init_b()
        last_write = bytes(mock.write_data.call_args_list[-1][0][0])
        self.assertEqual(last_write[0], 0x83)  # GET_BITS_HIGH


# ---------------------------------------------------------------------------
# jtag_shift argument validation
# ---------------------------------------------------------------------------

class TestJtagShiftValidation(unittest.TestCase):
    def test_zero_bits_raises_value_error(self):
        t, _ = _transport_with_mock()
        with self.assertRaises(ValueError):
            t.jtag_shift(b"\x00", 0)

    def test_tdi_too_short_raises_value_error(self):
        t, _ = _transport_with_mock()
        with self.assertRaises(ValueError):
            t.jtag_shift(b"\x00", 16)  # need 2 bytes, gave 1


# ---------------------------------------------------------------------------
# jtag_shift produces correct MPSSE command structure (8 bits, no last)
# ---------------------------------------------------------------------------

class TestJtagShiftCommands(unittest.TestCase):
    def test_8bit_shift_no_last(self):
        t, mock = _transport_with_mock()
        mock.read_data_bytes.return_value = bytearray([0xA5])
        result = t.jtag_shift(b"\xA5", 8, last=False)
        # Should have issued RW_BYTES_PVE_NVE_LSB for 1 byte
        all_cmds = b"".join(bytes(c[0][0]) for c in mock.write_data.call_args_list)
        self.assertIn(bytes([0x39, 0x00, 0x00, 0xA5]), all_cmds)
        self.assertEqual(result, bytes([0xA5]))

    def test_read_empty_writes_nothing(self):
        t, mock = _transport_with_mock()
        t.write(b"")
        # write_data should not have been called (no-op)
        mock.write_data.assert_not_called()

    def test_read_zero_bytes_returns_empty(self):
        t, mock = _transport_with_mock()
        result = t.read(0)
        self.assertEqual(result, b"")
        mock.write_data.assert_not_called()


# ---------------------------------------------------------------------------
# _pack_tdo unit tests (pure logic, no hardware)
# ---------------------------------------------------------------------------

class TestPackTdo(unittest.TestCase):
    def test_single_full_byte(self):
        raw = bytearray([0xC3])
        result = FtdiTransport._pack_tdo(raw, num_bits=8, pre_bits=8, last=False)
        self.assertEqual(result, bytes([0xC3]))

    def test_two_full_bytes(self):
        raw = bytearray([0x12, 0x34])
        result = FtdiTransport._pack_tdo(raw, num_bits=16, pre_bits=16, last=False)
        self.assertEqual(result, bytes([0x12, 0x34]))

    def test_output_length_matches_num_bits(self):
        for num_bits in range(1, 25):
            raw = bytearray(math.ceil(num_bits / 8) + 1)
            result = FtdiTransport._pack_tdo(raw, num_bits, num_bits, False)
            self.assertEqual(len(result), math.ceil(num_bits / 8), f"Failed for num_bits={num_bits}")

    def test_sub_byte_3_bits(self):
        # 3 bits: MPSSE left-aligns results (MSB = first received bit)
        # received bits [0,1,2] = 1,0,1 → raw MPSSE byte: 0b10100000 = 0xA0
        # Expected LSB-first output: bit0=1, bit1=0, bit2=1 → 0b00000101 = 0x05
        raw = bytearray([0xA0])
        result = FtdiTransport._pack_tdo(raw, num_bits=3, pre_bits=3, last=False)
        self.assertEqual(result, bytes([0x05]))

    def test_last_bit_captured(self):
        # 1 bit with last=True: TDO captured in bit7 of TMS response byte
        raw = bytearray([0x80])  # bit7 = 1
        result = FtdiTransport._pack_tdo(raw, num_bits=1, pre_bits=0, last=True)
        self.assertEqual(result, bytes([0x01]))


# ---------------------------------------------------------------------------
# close() is safe to call multiple times
# ---------------------------------------------------------------------------

class TestClose(unittest.TestCase):
    def test_close_when_not_open_is_safe(self):
        t = FtdiTransport()
        t.close()  # should not raise

    def test_close_disconnects(self):
        t, mock = _transport_with_mock()
        t.close()
        mock.close.assert_called_once()
        self.assertIsNone(t._ftdi)

    def test_double_close_is_safe(self):
        t, mock = _transport_with_mock()
        t.close()
        t.close()  # second call — _ftdi is now None, should not raise


# ---------------------------------------------------------------------------
# GPIO pin-mapping constants sanity checks
# ---------------------------------------------------------------------------

class TestConstants(unittest.TestCase):
    def test_program_b_is_output(self):
        self.assertTrue(_ACBUS_DIRECTION & (1 << _PROGRAM_B_BIT))

    def test_init_b_is_input(self):
        self.assertFalse(_ACBUS_DIRECTION & (1 << _INIT_B_BIT))

    def test_done_is_input(self):
        self.assertFalse(_ACBUS_DIRECTION & (1 << _DONE_BIT))

    def test_adbus_tdo_is_input(self):
        # TDO is ADBUS2; it should NOT be in the output direction mask
        TDO_BIT = 0x04
        self.assertFalse(_ADBUS_DIRECTION & TDO_BIT)

    def test_adbus_tck_tdi_tms_are_outputs(self):
        TCK_BIT = 0x01
        TDI_BIT = 0x02
        TMS_BIT = 0x08
        self.assertTrue(_ADBUS_DIRECTION & TCK_BIT)
        self.assertTrue(_ADBUS_DIRECTION & TDI_BIT)
        self.assertTrue(_ADBUS_DIRECTION & TMS_BIT)


if __name__ == "__main__":
    unittest.main()
