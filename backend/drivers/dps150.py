import struct
from .base import InstrumentDriver

INIT_PACKET = bytes([0xF1, 0xC1, 0x00, 0x01, 0x01, 0x02])

def build_packet(cmd: int, register: int, payload: bytes) -> bytes:
    length = len(payload)
    checksum = (register + length + sum(payload)) % 256
    return bytes([0xF1, cmd, register, length]) + payload + bytes([checksum])

def _parse_response(data: bytes) -> tuple[int, bytes] | None:
    """Return (register, payload) from a device response frame, or None."""
    if len(data) < 5 or data[0] != 0xF0 or data[1] != 0xA1:
        return None
    register = data[2]
    length = data[3]
    if len(data) < 4 + length + 1:
        return None
    payload = data[4:4 + length]
    expected_cksum = (register + length + sum(payload)) % 256
    if data[4 + length] != expected_cksum:
        return None
    return register, payload
