"""FTDI FT232H MPSSE transport layer for the Analog Discovery rev C.

This module owns the raw USB connection to the device and provides:
  - FPGA control GPIO (PROGRAM_B / INIT_B / DONE) via ACBUS
  - JTAG shift interface for bitstream loading via ADBUS
  - PTI bulk I/O interface for runtime instrument protocol via ADBUS

Pin mapping
-----------
ADBUS (MPSSE low byte):
  ADBUS0  TCK/CLK   output
  ADBUS1  TDI/DO    output
  ADBUS2  TDO/DI    input
  ADBUS3  TMS/CS    output
  ADBUS4-7          unused / reserved

ACBUS (MPSSE high byte):
  ACBUS0  PROGRAM_B  output, active-low (drive low to reset FPGA)
  ACBUS1  INIT_B     input  (low while FPGA clears config, high when ready)
  ACBUS2  DONE       input  (high when FPGA has loaded bitstream)
  ACBUS3-7           unused, set as inputs
"""

from __future__ import annotations

import math
from typing import Optional

from pyftdi.ftdi import Ftdi


# ---------------------------------------------------------------------------
# ADBUS direction / initial value masks
# ---------------------------------------------------------------------------
# Outputs: TCK(0), TDI(1), TMS(3) — TDO(2) is input
_ADBUS_DIRECTION: int = 0b00001011  # bits 0,1,3 = output; bit 2 = input
_ADBUS_IDLE: int = 0b00001000       # TCK=0, TDI=0, TMS=1 (idle-high)

# ---------------------------------------------------------------------------
# ACBUS direction / value masks
# ---------------------------------------------------------------------------
# PROGRAM_B on ACBUS0 = output; INIT_B(1) and DONE(2) = inputs
_ACBUS_DIRECTION: int = 0b00000001  # only ACBUS0 is an output
# PROGRAM_B released (high-Z via pull-up); the bit is written 1 so that when
# we switch it to output it drives high before asserting low.
_ACBUS_IDLE: int = 0b00000001       # PROGRAM_B deasserted (logic 1 = not reset)

# Bit positions within the ACBUS byte
_PROGRAM_B_BIT: int = 0  # ACBUS0
_INIT_B_BIT: int = 1     # ACBUS1
_DONE_BIT: int = 2       # ACBUS2

# ---------------------------------------------------------------------------
# Clock rate
# ---------------------------------------------------------------------------
# FT232H base clock in MPSSE mode: 60 MHz (after DIV5 disable), or 12 MHz
# with DIV5 enabled (default). The TCK_DIVISOR formula is:
#   f = base / ((1 + divisor) * 2)
# For 6 MHz from 60 MHz base: divisor = (60 / (6*2)) - 1 = 4 → 0x0004
# We request via open_mpsse_from_url which handles the divisor calculation.
_JTAG_FREQUENCY: float = 6.0e6  # 6 MHz

# Latency timer in milliseconds for USB micro-frame polling
_USB_LATENCY: int = 1


class TransportError(RuntimeError):
    """Raised for transport-level errors (device not found, I/O failure, …)."""


class FtdiTransport:
    """Low-level USB transport for the Digilent Analog Discovery (FT232H).

    Callers must invoke :py:meth:`open` before using any other method and
    :py:meth:`close` when finished.

    Thread safety
    -------------
    This class is **not** thread-safe. Callers must serialise access.
    """

    # Default pyftdi URL for the Analog Discovery FT232H (interface 1)
    DEFAULT_URL: str = "ftdi://0x0403:0x6014/1"

    def __init__(self) -> None:
        self._ftdi: Optional[Ftdi] = None
        # Shadow of the current ACBUS value so we can do read-modify-write
        # without issuing a GPIO read each time.
        self._acbus_value: int = _ACBUS_IDLE

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self, url: str = DEFAULT_URL) -> None:
        """Open the FT232H and configure MPSSE for JTAG + GPIO control.

        Args:
            url: pyftdi device URL, e.g. ``"ftdi://0x0403:0x6014/1"``.

        Raises:
            TransportError: If the device cannot be found or opened.
        """
        if self._ftdi is not None and self._ftdi.is_connected:
            raise TransportError("Transport already open; call close() first")

        ftdi = Ftdi()
        try:
            ftdi.open_mpsse_from_url(
                url,
                direction=_ADBUS_DIRECTION,
                initial=_ADBUS_IDLE,
                frequency=_JTAG_FREQUENCY,
                latency=_USB_LATENCY,
            )
        except Exception as exc:
            raise TransportError(f"Failed to open FTDI device at {url!r}: {exc}") from exc

        self._ftdi = ftdi

        # Disable loopback (should already be off, but be explicit)
        self._ftdi.write_data(bytearray([Ftdi.LOOPBACK_END]))

        # Initialise ACBUS: PROGRAM_B as output-high (deasserted), INIT_B and
        # DONE as inputs.
        self._acbus_value = _ACBUS_IDLE
        self._write_acbus(self._acbus_value)

        # Flush any stale RX data
        self._ftdi.purge_buffers()

    def close(self) -> None:
        """Close the USB connection and release the FTDI device.

        Safe to call even if the device was never opened or has already been
        closed.
        """
        if self._ftdi is not None and self._ftdi.is_connected:
            try:
                self._ftdi.close()
            except Exception:
                pass
        self._ftdi = None

    # ------------------------------------------------------------------
    # FPGA control GPIO helpers
    # ------------------------------------------------------------------

    def assert_program_b(self) -> None:
        """Drive PROGRAM_B low, putting the FPGA into reset/reprogram mode."""
        self._require_open()
        self._acbus_value &= ~(1 << _PROGRAM_B_BIT)
        self._write_acbus(self._acbus_value)

    def release_program_b(self) -> None:
        """Release PROGRAM_B high (via on-board pull-up), ending FPGA reset."""
        self._require_open()
        self._acbus_value |= (1 << _PROGRAM_B_BIT)
        self._write_acbus(self._acbus_value)

    def read_init_b(self) -> bool:
        """Read the current level of INIT_B from the FPGA.

        Returns:
            ``True`` if INIT_B is high (FPGA is ready to receive bitstream),
            ``False`` if low (FPGA is still clearing its configuration memory).
        """
        self._require_open()
        gpio = self._read_acbus()
        return bool(gpio & (1 << _INIT_B_BIT))

    def read_done(self) -> bool:
        """Read the current level of DONE from the FPGA.

        Returns:
            ``True`` if DONE is high (bitstream loaded successfully),
            ``False`` otherwise.
        """
        self._require_open()
        gpio = self._read_acbus()
        return bool(gpio & (1 << _DONE_BIT))

    # ------------------------------------------------------------------
    # JTAG shift (bitstream loading)
    # ------------------------------------------------------------------

    def jtag_shift(
        self,
        tdi: bytes,
        num_bits: int,
        last: bool = False,
    ) -> bytes:
        """Shift *num_bits* bits into TDI and return the corresponding TDO bits.

        Data is shifted LSB-first, matching Spartan-6 JTAG requirements.
        The caller is responsible for JTAG state-machine navigation (TMS);
        this method shifts data only (TMS=0 throughout, except optionally on
        the last bit when *last* is ``True`` to exit Shift-DR/IR).

        Args:
            tdi: Bytes containing the bits to shift out, LSB of byte 0 first.
                 Must contain at least ``ceil(num_bits / 8)`` bytes.
            num_bits: Number of bits to shift (must be >= 1).
            last: If ``True``, assert TMS=1 on the final bit to transition
                  the TAP out of the Shift state.

        Returns:
            Bytes containing the captured TDO bits, packed LSB-first in the
            same layout as *tdi*.

        Raises:
            TransportError: If the device is not open or an I/O error occurs.
            ValueError: If *num_bits* is zero or *tdi* is too short.
        """
        self._require_open()
        if num_bits < 1:
            raise ValueError("num_bits must be >= 1")
        needed_bytes = math.ceil(num_bits / 8)
        if len(tdi) < needed_bytes:
            raise ValueError(
                f"tdi too short: need {needed_bytes} bytes for {num_bits} bits, "
                f"got {len(tdi)}"
            )

        cmd = bytearray()
        tdo_bytes_expected = 0

        if last:
            # Shift all bits except the last as normal, then shift the last bit
            # with TMS=1 to exit Shift state.
            pre_bits = num_bits - 1
        else:
            pre_bits = num_bits

        # --- Bulk byte transfers ---
        byte_count = pre_bits // 8
        if byte_count:
            # RW_BYTES_PVE_NVE_LSB: clock data bytes out on -ve edge, in on +ve
            # edge, LSB first.  Length field is (N-1).
            blen = byte_count - 1
            cmd += bytearray([
                Ftdi.RW_BYTES_PVE_NVE_LSB,
                blen & 0xFF,
                (blen >> 8) & 0xFF,
            ])
            cmd += tdi[:byte_count]
            tdo_bytes_expected += byte_count

        # --- Trailing sub-byte bits (non-last) ---
        rem_bits = pre_bits % 8
        if rem_bits:
            # RW_BITS_PVE_NVE_LSB: clock N bits, length field = N-1
            cmd += bytearray([
                Ftdi.RW_BITS_PVE_NVE_LSB,
                rem_bits - 1,
                tdi[byte_count],
            ])
            tdo_bytes_expected += 1  # TDO packed into one byte (bits 7..MSB)

        # --- Last bit with optional TMS transition ---
        if last:
            # Use RW_BITS_TMS_PVE_NVE: clocks 1 TMS bit + 1 TDI bit, captures TDO.
            # Byte layout: [TDI bit in bit0, TMS in bit7]
            last_byte_idx = (num_bits - 1) // 8
            last_bit_pos = (num_bits - 1) % 8
            tdi_last_bit = (tdi[last_byte_idx] >> last_bit_pos) & 1
            tms_tdi_byte = 0x80 | tdi_last_bit  # TMS=1, TDI=last bit
            cmd += bytearray([
                Ftdi.RW_BITS_TMS_PVE_NVE,
                0,           # clock 1 TMS bit (length = N-1 = 0)
                tms_tdi_byte,
            ])
            tdo_bytes_expected += 1

        # Flush all chunks except the final one before appending SEND_IMMEDIATE,
        # so large bitstreams (1-4 MB) never build one giant buffer in memory.
        _CHUNK = 65536
        if len(cmd) > _CHUNK:
            offset = 0
            while offset + _CHUNK < len(cmd):
                self._ftdi.write_data(cmd[offset: offset + _CHUNK])
                offset += _CHUNK
            cmd = cmd[offset:]  # keep the remainder for the final flush

        cmd += bytearray([Ftdi.SEND_IMMEDIATE])

        self._ftdi.write_data(cmd)
        raw = self._ftdi.read_data_bytes(tdo_bytes_expected, attempt=4)  # retry up to 4 USB micro-frames

        return self._pack_tdo(raw, num_bits, pre_bits, last)

    # ------------------------------------------------------------------
    # PTI bulk I/O (runtime protocol)
    # ------------------------------------------------------------------

    def write(self, data: bytes) -> None:
        """Write raw bytes to the FTDI TX FIFO for the PTI protocol.

        Data is sent as a raw MPSSE byte-write command sequence so it passes
        through the same USB interface used for JTAG, after the FPGA has been
        configured and the JTAG TAP is parked in Run-Test/Idle.

        Args:
            data: Bytes to transmit.

        Raises:
            TransportError: If the device is not open or an I/O error occurs.
        """
        self._require_open()
        if not data:
            return

        # Send as MPSSE WRITE_BYTES command (no read-back).
        # WRITE_BYTES_NVE_LSB shifts on negative clock edge, LSB first.
        cmd = bytearray()
        offset = 0
        while offset < len(data):
            chunk = data[offset: offset + 65536]
            blen = len(chunk) - 1
            cmd += bytearray([
                Ftdi.WRITE_BYTES_NVE_LSB,
                blen & 0xFF,
                (blen >> 8) & 0xFF,
            ])
            cmd += chunk
            offset += len(chunk)

        self._ftdi.write_data(cmd)

    def read(self, n: int) -> bytes:
        """Read *n* bytes from the FTDI RX FIFO for the PTI protocol.

        Issues a MPSSE READ_BYTES command so the FTDI clocks *n* bytes in from
        TDO while driving TDI and TCK.

        Args:
            n: Number of bytes to read.

        Returns:
            Exactly *n* bytes from the device.

        Raises:
            TransportError: If the device is not open, or fewer than *n* bytes
                            are returned.
        """
        self._require_open()
        if n == 0:
            return b""

        cmd = bytearray()
        offset = 0
        while offset < n:
            chunk_n = min(n - offset, 65536)
            blen = chunk_n - 1
            cmd += bytearray([
                Ftdi.READ_BYTES_PVE_LSB,
                blen & 0xFF,
                (blen >> 8) & 0xFF,
            ])
            offset += chunk_n

        cmd += bytearray([Ftdi.SEND_IMMEDIATE])
        self._ftdi.write_data(cmd)

        result = self._ftdi.read_data_bytes(n, attempt=4)  # retry up to 4 USB micro-frames
        if len(result) != n:
            raise TransportError(
                f"PTI read expected {n} bytes, got {len(result)}"
            )
        return bytes(result)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_open(self) -> None:
        if self._ftdi is None or not self._ftdi.is_connected:
            raise TransportError(
                "Transport is not open. Call open() before using this method."
            )

    def _write_acbus(self, value: int) -> None:
        """Write *value* to the ACBUS GPIO high byte, preserving direction."""
        cmd = bytearray([Ftdi.SET_BITS_HIGH, value & 0xFF, _ACBUS_DIRECTION])
        self._ftdi.write_data(cmd)

    def _read_acbus(self) -> int:
        """Return the current sampled ACBUS GPIO high byte (all 8 bits)."""
        cmd = bytearray([Ftdi.GET_BITS_HIGH, Ftdi.SEND_IMMEDIATE])
        self._ftdi.write_data(cmd)
        data = self._ftdi.read_data_bytes(1, attempt=4)  # retry up to 4 USB micro-frames
        if not data:
            raise TransportError("Failed to read ACBUS GPIO state")
        return data[0]

    @staticmethod
    def _pack_tdo(raw: bytearray, num_bits: int, pre_bits: int, last: bool) -> bytes:
        """Re-pack raw MPSSE TDO output into a compact bit buffer.

        MPSSE returns data in a slightly inconvenient format:
        - Full byte transfers return bits right-aligned (LSB = first bit received).
        - Sub-byte transfers return bits left-aligned in the byte
          (bit 7 = first bit received).
        - TMS+TDI transfers return the captured TDO bit in bit 7.

        This helper normalises everything into a single LSB-first packed
        byte array matching the *tdi* layout.

        Args:
            raw: Raw bytes received from MPSSE (in read order).
            num_bits: Total number of bits that were shifted.
            pre_bits: Number of bits shifted before the (optional) last bit.
            last: Whether a TMS-last byte was appended.

        Returns:
            Packed TDO bytes, same length as ``ceil(num_bits / 8)``.
        """
        out_bytes = bytearray(math.ceil(num_bits / 8))
        raw_idx = 0
        bit_cursor = 0  # next output bit position (LSB-first in out_bytes)

        # Consume full-byte TDO data
        full_bytes = pre_bits // 8
        for i in range(full_bytes):
            byte_pos = bit_cursor // 8
            bit_off = bit_cursor % 8
            if bit_off == 0:
                out_bytes[byte_pos] = raw[raw_idx]
            else:
                out_bytes[byte_pos] |= (raw[raw_idx] << bit_off) & 0xFF
                if byte_pos + 1 < len(out_bytes):
                    out_bytes[byte_pos + 1] |= raw[raw_idx] >> (8 - bit_off)
            bit_cursor += 8
            raw_idx += 1

        # Consume sub-byte TDO data (bits left-aligned in the raw byte)
        rem = pre_bits % 8
        if rem:
            # MPSSE puts the first received bit in bit7, second in bit6, …
            # We need to reverse the bit order to get LSB-first.
            raw_byte = raw[raw_idx]
            raw_idx += 1
            for b in range(rem):
                # bit (rem-1-b) of raw_byte is received bit b
                bit_val = (raw_byte >> (7 - b)) & 1
                byte_pos = bit_cursor // 8
                bit_off = bit_cursor % 8
                out_bytes[byte_pos] |= bit_val << bit_off
                bit_cursor += 1

        # Consume TMS-last TDO byte (TDO captured in bit 7)
        if last:
            tdo_bit = (raw[raw_idx] >> 7) & 1
            byte_pos = bit_cursor // 8
            bit_off = bit_cursor % 8
            if byte_pos < len(out_bytes):
                out_bytes[byte_pos] |= tdo_bit << bit_off

        return bytes(out_bytes)
