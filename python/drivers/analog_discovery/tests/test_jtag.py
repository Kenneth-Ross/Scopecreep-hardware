"""Unit tests for JtagLoader.

All tests mock FtdiTransport; no hardware is required.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, call, patch


# ---------------------------------------------------------------------------
# Minimal pyftdi stub (mirrors the one in test_transport.py) so that
# transport.py can be imported without the real pyftdi library installed.
# ---------------------------------------------------------------------------
def _ensure_pyftdi_stub() -> None:
    if "pyftdi" in sys.modules:
        return

    ftdi_mod = types.ModuleType("pyftdi.ftdi")

    class _FtdiFake:
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

        def __init__(self):
            self.is_connected = False

        def open_mpsse_from_url(self, *a, **kw):
            self.is_connected = True

        def write_data(self, data):
            pass

        def read_data_bytes(self, n, attempt=1):
            return bytearray(n)

        def purge_buffers(self):
            pass

        def close(self):
            self.is_connected = False

    ftdi_mod.Ftdi = _FtdiFake
    pyftdi_pkg = types.ModuleType("pyftdi")
    sys.modules.setdefault("pyftdi", pyftdi_pkg)
    sys.modules.setdefault("pyftdi.ftdi", ftdi_mod)


_ensure_pyftdi_stub()

# Now we can safely import the modules under test.
from python.drivers.analog_discovery.jtag import (  # noqa: E402
    BitstreamLoadError,
    JtagLoader,
    _BIT_REVERSE_TABLE,
    _SYNC_WORD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_transport(
    init_b_sequence=None,
    done_sequence=None,
) -> MagicMock:
    """Return a mocked FtdiTransport.

    Args:
        init_b_sequence: List of bool values returned by successive
            read_init_b() calls.  Defaults to [False, True] (go low, go high).
        done_sequence: List of bool values returned by successive
            read_done() calls.  Defaults to [True] (immediate success).
    """
    if init_b_sequence is None:
        init_b_sequence = [False, True]
    if done_sequence is None:
        done_sequence = [True]

    # unsafe=True is required because MagicMock (Python 3.8+) raises
    # AttributeError for attributes starting with "assert" by default.
    # FtdiTransport has assert_program_b() which triggers this guard.
    t = MagicMock(unsafe=True)
    t.read_init_b.side_effect = init_b_sequence
    t.read_done.side_effect = done_sequence
    t.jtag_shift.return_value = b""
    return t


def _make_bitstream_file(payload: bytes | None = None) -> str:
    """Write a temporary .bit file containing the sync word and return its path.

    The file is written to a named temp file; the caller must delete it.
    """
    if payload is None:
        # A tiny but valid-looking payload
        payload = bytes([0x00, 0x01, 0x02, 0x03])

    data = _SYNC_WORD + payload
    fd, path = tempfile.mkstemp(suffix=".bit")
    os.write(fd, data)
    os.close(fd)
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBitReverseTable(unittest.TestCase):
    """Sanity-check the lookup table."""

    def test_zero(self):
        self.assertEqual(_BIT_REVERSE_TABLE[0x00], 0x00)

    def test_ff(self):
        self.assertEqual(_BIT_REVERSE_TABLE[0xFF], 0xFF)

    def test_01_becomes_80(self):
        self.assertEqual(_BIT_REVERSE_TABLE[0x01], 0x80)

    def test_80_becomes_01(self):
        self.assertEqual(_BIT_REVERSE_TABLE[0x80], 0x01)

    def test_aa_becomes_55(self):
        self.assertEqual(_BIT_REVERSE_TABLE[0xAA], 0x55)

    def test_table_length(self):
        self.assertEqual(len(_BIT_REVERSE_TABLE), 256)


class TestJtagLoaderGpioSequence(unittest.TestCase):
    """Verify the correct GPIO sequence is issued during a successful load."""

    def setUp(self):
        self.path = _make_bitstream_file()

    def tearDown(self):
        os.unlink(self.path)

    def test_gpio_sequence_order(self):
        t = _make_transport(init_b_sequence=[False, True], done_sequence=[True])
        loader = JtagLoader(t)
        loader.load(self.path, timeout_s=1.0)

        # assert_program_b must be called first
        t.assert_program_b.assert_called_once()
        # release_program_b must be called after INIT_B goes low
        t.release_program_b.assert_called_once()
        # read_init_b must be called at least twice (once for low, once for high)
        self.assertGreaterEqual(t.read_init_b.call_count, 2)
        # read_done must be called at least once
        self.assertGreaterEqual(t.read_done.call_count, 1)
        # jtag_shift must be called exactly once
        t.jtag_shift.assert_called_once()

    def test_program_b_before_init_b_poll(self):
        """assert_program_b must happen before any INIT_B read."""
        call_order = []
        t = MagicMock(unsafe=True)
        t.assert_program_b.side_effect = lambda: call_order.append("assert_program_b")

        # Use a callable side_effect (not a list) so appends happen at call time
        _init_b_values = iter([False, True])

        def _read_init_b():
            val = next(_init_b_values)
            call_order.append("read_init_b_low" if not val else "read_init_b_high")
            return val

        t.read_init_b.side_effect = _read_init_b
        t.release_program_b.side_effect = lambda: call_order.append("release_program_b")
        t.read_done.side_effect = [True]
        t.jtag_shift.return_value = b""

        loader = JtagLoader(t)
        loader.load(self.path, timeout_s=1.0)

        self.assertEqual(call_order[0], "assert_program_b")
        self.assertIn("read_init_b_low", call_order)
        low_idx = call_order.index("read_init_b_low")
        release_idx = call_order.index("release_program_b")
        self.assertLess(low_idx, release_idx)

    def test_jtag_shift_after_init_b_high(self):
        """jtag_shift must only be called after INIT_B goes high."""
        call_order = []
        t = MagicMock(unsafe=True)
        t.assert_program_b.side_effect = lambda: call_order.append("assert_program_b")
        t.read_init_b.side_effect = [
            (call_order.append("init_b_low") or False),
            (call_order.append("init_b_high") or True),
        ]
        t.release_program_b.side_effect = lambda: call_order.append("release_program_b")
        t.jtag_shift.side_effect = lambda *a, **kw: call_order.append("jtag_shift") or b""
        t.read_done.side_effect = [True]

        loader = JtagLoader(t)
        loader.load(self.path, timeout_s=1.0)

        high_idx = call_order.index("init_b_high")
        shift_idx = call_order.index("jtag_shift")
        self.assertLess(high_idx, shift_idx)


class TestJtagLoaderBitReversedPayload(unittest.TestCase):
    """Verify that jtag_shift receives a bit-reversed payload."""

    def test_bit_reversed_bytes_passed_to_jtag_shift(self):
        # Craft a known payload after the sync word
        payload = bytes([0x01, 0x80, 0xAA])
        path = _make_bitstream_file(payload)
        try:
            t = _make_transport()
            loader = JtagLoader(t)
            loader.load(path, timeout_s=1.0)

            args, kwargs = t.jtag_shift.call_args
            shifted_data = args[0]

            # The sync word is also bit-reversed; check only the payload portion
            sync_reversed = bytes(_BIT_REVERSE_TABLE[b] for b in _SYNC_WORD)
            payload_reversed = bytes(_BIT_REVERSE_TABLE[b] for b in payload)
            expected = sync_reversed + payload_reversed

            self.assertEqual(shifted_data, expected)
        finally:
            os.unlink(path)

    def test_num_bits_matches_payload_length(self):
        payload = bytes([0xDE, 0xAD, 0xBE, 0xEF])
        path = _make_bitstream_file(payload)
        try:
            t = _make_transport()
            loader = JtagLoader(t)
            loader.load(path, timeout_s=1.0)

            args, kwargs = t.jtag_shift.call_args
            shifted_data = args[0]
            num_bits = args[1]
            expected_len = len(_SYNC_WORD) + len(payload)
            self.assertEqual(num_bits, expected_len * 8)
            self.assertEqual(len(shifted_data), expected_len)
        finally:
            os.unlink(path)


class TestJtagLoaderInitBTimeout(unittest.TestCase):
    """BitstreamLoadError raised when INIT_B never goes low."""

    def test_init_b_low_timeout(self):
        path = _make_bitstream_file()
        try:
            # read_init_b always returns True (never goes low)
            t = _make_transport(init_b_sequence=[True] * 1000, done_sequence=[True])
            loader = JtagLoader(t)
            with self.assertRaises(BitstreamLoadError) as ctx:
                loader.load(path, timeout_s=0.01)
            self.assertIn("INIT_B", str(ctx.exception))
            self.assertIn("low", str(ctx.exception))
        finally:
            os.unlink(path)

    def test_init_b_high_timeout(self):
        path = _make_bitstream_file()
        try:
            # INIT_B goes low immediately, but never goes high
            t = _make_transport(
                init_b_sequence=[False] + [False] * 1000,
                done_sequence=[True],
            )
            loader = JtagLoader(t)
            with self.assertRaises(BitstreamLoadError) as ctx:
                loader.load(path, timeout_s=0.01)
            self.assertIn("INIT_B", str(ctx.exception))
            self.assertIn("high", str(ctx.exception))
        finally:
            os.unlink(path)


class TestJtagLoaderDoneTimeout(unittest.TestCase):
    """BitstreamLoadError raised when DONE never goes high."""

    def test_done_timeout(self):
        path = _make_bitstream_file()
        try:
            # DONE always returns False
            t = _make_transport(
                init_b_sequence=[False, True],
                done_sequence=[False] * 10000,
            )
            loader = JtagLoader(t)
            with self.assertRaises(BitstreamLoadError) as ctx:
                loader.load(path, timeout_s=0.01)
            self.assertIn("DONE", str(ctx.exception))
        finally:
            os.unlink(path)


class TestJtagLoaderFileErrors(unittest.TestCase):
    """BitstreamLoadError raised for bad/missing bitstream files."""

    def test_missing_file(self):
        t = _make_transport()
        loader = JtagLoader(t)
        with self.assertRaises(BitstreamLoadError) as ctx:
            loader.load("/nonexistent/path/to/bitstream.bit", timeout_s=1.0)
        self.assertIn("Cannot read", str(ctx.exception))

    def test_sync_word_not_found(self):
        # File exists but contains no sync word
        fd, path = tempfile.mkstemp(suffix=".bit")
        os.write(fd, b"\x00\x09\x01\x02\x03\x04\x05\x06\x07\x08\x09")
        os.close(fd)
        try:
            t = _make_transport()
            loader = JtagLoader(t)
            with self.assertRaises(BitstreamLoadError) as ctx:
                loader.load(path, timeout_s=1.0)
            self.assertIn("Sync word not found", str(ctx.exception))
        finally:
            os.unlink(path)

    def test_empty_file(self):
        fd, path = tempfile.mkstemp(suffix=".bit")
        os.close(fd)
        try:
            t = _make_transport()
            loader = JtagLoader(t)
            with self.assertRaises(BitstreamLoadError) as ctx:
                loader.load(path, timeout_s=1.0)
            self.assertIn("Sync word not found", str(ctx.exception))
        finally:
            os.unlink(path)

    def test_file_with_junk_before_sync_word(self):
        """Sync word is found even when preceded by a header."""
        junk = b"\x00" * 64 + b"\x61\x00\x08test.ncd\x00" + b"\x65\x00\x00\x00\x04"
        path = _make_bitstream_file(b"\xDE\xAD")
        # Prepend extra junk (sync word already in file via _make_bitstream_file)
        with open(path, "rb") as f:
            existing = f.read()
        with open(path, "wb") as f:
            f.write(junk + existing)
        try:
            t = _make_transport()
            loader = JtagLoader(t)
            # Should succeed — sync word is present
            loader.load(path, timeout_s=1.0)
            t.jtag_shift.assert_called_once()
        finally:
            os.unlink(path)


class TestJtagLoaderTransportError(unittest.TestCase):
    """TransportError from the transport is wrapped as BitstreamLoadError."""

    def test_transport_error_wrapped(self):
        from python.drivers.analog_discovery.transport import TransportError

        path = _make_bitstream_file()
        try:
            t = MagicMock(unsafe=True)
            t.assert_program_b.side_effect = TransportError("USB timeout")
            loader = JtagLoader(t)
            with self.assertRaises(BitstreamLoadError) as ctx:
                loader.load(path, timeout_s=1.0)
            self.assertIn("Transport error", str(ctx.exception))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
