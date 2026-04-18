"""PTI (Parallel Trace Interface) command protocol for the Digilent Analog Discovery.

This module implements the runtime command protocol that controls all instruments
on the Analog Discovery after the FPGA bitstream has been loaded.  It sits above
:class:`FtdiTransport` and is consumed by the higher-level instrument modules
(oscilloscope, AWG, PSU, DIO).

Frame format (8-byte header + optional payload)
-----------------------------------------------
Offset  Size  Content
0       4     Zero padding: 0x00 0x00 0x00 0x00
4       1     Payload length - 1  (0 if payload is empty)
5       1     Protocol type: 0x02
6       1     Command byte
7       1     Port/channel selector (0-indexed)
[8..]   N     Payload bytes
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .transport import FtdiTransport, TransportError  # noqa: F401 (re-exported for callers)

# ---------------------------------------------------------------------------
# Command byte constants
# ---------------------------------------------------------------------------

# Oscilloscope
CMD_SCOPE_CONFIGURE = 0x01  # payload: ch(1), range_code(1), coupling_code(1), offset_raw(2)
CMD_SCOPE_ARM       = 0x02  # payload: source(1), level_raw(2), edge(1), pretrig(2)
CMD_SCOPE_READ      = 0x03  # response payload: N×2 bytes (16-bit ADC words, signed)

# AWG
CMD_AWG_DATA        = 0x08  # payload: N×2 bytes (16-bit DAC words)
CMD_AWG_ENABLE      = 0x09  # payload: 1 byte (0x00=off, 0x01=on)

# Power supply
CMD_PSU_VPOS        = 0x10  # payload: 2 bytes voltage DAC code for V+ rail
CMD_PSU_VNEG        = 0x11  # payload: 2 bytes voltage DAC code for V- rail

# Digital I/O
CMD_DIO_DIR         = 0x14  # payload: 2 bytes direction mask (1=output)
CMD_DIO_WRITE       = 0x15  # payload: 2 bytes output values
CMD_DIO_READ        = 0x16  # response payload: 2 bytes pin state


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class PTICommand:
    """Represents a single PTI command to be sent to the device.

    Attributes:
        cmd: Command byte (one of the ``CMD_*`` constants).
        port: Port/channel selector (0-indexed).
        payload: Raw payload bytes for the command.
        response_size: Expected number of payload bytes in the response.
            For write-only commands the device returns an 8-byte ACK with
            no payload, so this should be left at 0.  For read commands
            (e.g. :data:`CMD_SCOPE_READ`, :data:`CMD_DIO_READ`) set this
            to the number of *payload* bytes expected (not including the
            8-byte response header).
    """

    cmd: int
    port: int
    payload: bytes = field(default=b"")
    response_size: int = 0


class PTIError(Exception):
    """Raised when a PTI protocol error is detected (e.g. unexpected response)."""


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

class PTIController:
    """High-level PTI command controller.

    Wraps an :class:`~.transport.FtdiTransport` and provides :meth:`send` to
    issue PTI commands and retrieve their response payloads.

    Thread safety
    -------------
    This class is **not** thread-safe.  The underlying transport must be opened
    before :meth:`send` is called.
    """

    PROTOCOL_TYPE: int = 0x02

    def __init__(self, transport: FtdiTransport) -> None:
        self._transport = transport

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(self, cmd: PTICommand) -> bytes:
        """Send *cmd* to the device and return the response payload bytes.

        For write-only commands the device returns an 8-byte ACK header with
        an empty payload; in that case an empty :class:`bytes` object is
        returned.

        For commands that return data (e.g. oscilloscope read), set
        ``cmd.response_size`` to the expected number of payload bytes so that
        the full response frame is read correctly.

        Args:
            cmd: The command to send.

        Returns:
            The response payload bytes (may be empty for ACK-only commands).

        Raises:
            PTIError: If the response header contains unexpected values.
            TransportError: If a transport-level I/O error occurs.
        """
        packed = self._pack(cmd)
        self._transport.write(packed)

        total_response_len = 8 + cmd.response_size
        raw = self._transport.read(total_response_len)
        return self._unpack_response(cmd, raw)

    def _pack(self, cmd: PTICommand) -> bytes:
        """Build the 8-byte header + payload bytes for *cmd*.

        The length byte at offset 4 follows the Digilent convention:
        - ``payload length - 1`` when the payload is non-empty.
        - ``0`` when the payload is empty (not ``0xFF``).

        Args:
            cmd: The command to pack.

        Returns:
            The fully-formed frame ready for transmission.
        """
        payload = cmd.payload
        if len(payload) > 256:
            raise ValueError(
                f"PTI payload too large: {len(payload)} bytes, protocol maximum is 256"
            )
        length_byte = (len(payload) - 1) if payload else 0
        header = bytes([
            0x00, 0x00, 0x00, 0x00,  # zero padding
            length_byte & 0xFF,
            self.PROTOCOL_TYPE,
            cmd.cmd & 0xFF,
            cmd.port & 0xFF,
        ])
        return header + payload

    def _unpack_response(self, cmd: PTICommand, raw: bytes) -> bytes:
        """Validate *raw* and extract the response payload.

        Checks that the protocol type byte (offset 5) and command byte
        (offset 6) in the response header match the sent command.

        Args:
            cmd: The command that was sent (used for validation).
            raw: The raw bytes received from the device (header + payload).

        Returns:
            The payload portion of the response (bytes after the 8-byte header).

        Raises:
            PTIError: If the response header fields do not match expectations.
        """
        if len(raw) < 8:
            raise PTIError(
                f"Response too short: expected at least 8 bytes, got {len(raw)}"
            )

        proto_byte = raw[5]
        cmd_byte   = raw[6]

        if proto_byte != self.PROTOCOL_TYPE:
            raise PTIError(
                f"Protocol type mismatch: expected 0x{self.PROTOCOL_TYPE:02X}, "
                f"got 0x{proto_byte:02X}"
            )

        if cmd_byte != cmd.cmd:
            raise PTIError(
                f"Command byte mismatch: expected 0x{cmd.cmd:02X}, "
                f"got 0x{cmd_byte:02X}"
            )

        actual_payload_len = len(raw) - 8
        if actual_payload_len < cmd.response_size:
            raise PTIError(
                f"Response payload too short: expected {cmd.response_size} bytes, "
                f"got {actual_payload_len}"
            )

        return raw[8:]


# ---------------------------------------------------------------------------
# Convenience command constructors
# ---------------------------------------------------------------------------

def scope_configure(
    ch: int,
    range_code: int,
    coupling_code: int,
    offset_raw: int,
) -> PTICommand:
    """Build a :data:`CMD_SCOPE_CONFIGURE` command.

    Args:
        ch: Channel index (0-indexed).
        range_code: Voltage range code.
        coupling_code: Coupling mode code.
        offset_raw: Signed 16-bit offset DAC value.

    Returns:
        Ready-to-send :class:`PTICommand`.
    """
    payload = struct.pack(">BBBh", ch, range_code, coupling_code, offset_raw)
    return PTICommand(cmd=CMD_SCOPE_CONFIGURE, port=ch, payload=payload)


def scope_arm(
    source: int,
    level_raw: int,
    edge: int,
    pretrig: int,
) -> PTICommand:
    """Build a :data:`CMD_SCOPE_ARM` command.

    Args:
        source: Trigger source selector.
        level_raw: Signed 16-bit trigger level.
        edge: Edge polarity (e.g. 0=rising, 1=falling).
        pretrig: Pre-trigger sample count (unsigned 16-bit).

    Returns:
        Ready-to-send :class:`PTICommand`.
    """
    payload = struct.pack(">BhBH", source, level_raw, edge, pretrig)
    # port=0: trigger source is encoded in the payload, not the port field
    return PTICommand(cmd=CMD_SCOPE_ARM, port=0, payload=payload)


def scope_read(ch: int, n_samples: int) -> PTICommand:
    """Build a :data:`CMD_SCOPE_READ` command.

    Args:
        ch: Channel index (0-indexed).
        n_samples: Number of 16-bit ADC samples to read back.

    Returns:
        Ready-to-send :class:`PTICommand` with ``response_size`` set to
        ``n_samples * 2``.
    """
    return PTICommand(
        cmd=CMD_SCOPE_READ,
        port=ch,
        payload=b"",
        response_size=n_samples * 2,
    )


def awg_data(ch: int, dac_words: bytes) -> PTICommand:
    """Build a :data:`CMD_AWG_DATA` command.

    Args:
        ch: AWG channel index (0-indexed).
        dac_words: Raw 16-bit DAC words as a packed byte string
                   (length must be a multiple of 2).

    Returns:
        Ready-to-send :class:`PTICommand`.
    """
    if len(dac_words) % 2 != 0:
        raise ValueError("dac_words length must be a multiple of 2 (16-bit words)")
    return PTICommand(cmd=CMD_AWG_DATA, port=ch, payload=dac_words)


def awg_enable(ch: int, enabled: bool) -> PTICommand:
    """Build a :data:`CMD_AWG_ENABLE` command.

    Args:
        ch: AWG channel index (0-indexed).
        enabled: ``True`` to enable output, ``False`` to disable.

    Returns:
        Ready-to-send :class:`PTICommand`.
    """
    payload = bytes([0x01 if enabled else 0x00])
    return PTICommand(cmd=CMD_AWG_ENABLE, port=ch, payload=payload)


def psu_vpos(dac_code: int) -> PTICommand:
    """Build a :data:`CMD_PSU_VPOS` command.

    Args:
        dac_code: 16-bit unsigned DAC code for the V+ rail.

    Returns:
        Ready-to-send :class:`PTICommand`.
    """
    payload = struct.pack(">H", dac_code)
    return PTICommand(cmd=CMD_PSU_VPOS, port=0, payload=payload)


def psu_vneg(dac_code: int) -> PTICommand:
    """Build a :data:`CMD_PSU_VNEG` command.

    Args:
        dac_code: 16-bit unsigned DAC code for the V- rail.

    Returns:
        Ready-to-send :class:`PTICommand`.
    """
    payload = struct.pack(">H", dac_code)
    return PTICommand(cmd=CMD_PSU_VNEG, port=0, payload=payload)


def dio_set_dir(mask: int) -> PTICommand:
    """Build a :data:`CMD_DIO_DIR` command.

    Args:
        mask: 16-bit direction mask (1=output, 0=input per pin).

    Returns:
        Ready-to-send :class:`PTICommand`.
    """
    payload = struct.pack(">H", mask)
    return PTICommand(cmd=CMD_DIO_DIR, port=0, payload=payload)


def dio_write(value: int) -> PTICommand:
    """Build a :data:`CMD_DIO_WRITE` command.

    Args:
        value: 16-bit output value to drive on the DIO pins.

    Returns:
        Ready-to-send :class:`PTICommand`.
    """
    payload = struct.pack(">H", value)
    return PTICommand(cmd=CMD_DIO_WRITE, port=0, payload=payload)


def dio_read() -> PTICommand:
    """Build a :data:`CMD_DIO_READ` command.

    Returns:
        Ready-to-send :class:`PTICommand` with ``response_size=2``.
    """
    return PTICommand(
        cmd=CMD_DIO_READ,
        port=0,
        payload=b"",
        response_size=2,
    )
