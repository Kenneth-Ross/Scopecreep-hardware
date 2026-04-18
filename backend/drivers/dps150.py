import serial
import struct
import time
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


_INTER_CMD_DELAY = 0.05  # 50 ms — device requires this between commands

class DPS150(InstrumentDriver):
    def __init__(self, port: str, baud: int = 115200, timeout: float = 0.5):
        self._port = port
        self._baud = baud
        self._timeout = timeout
        self._ser: serial.Serial | None = None

    def connect(self) -> None:
        self._ser = serial.Serial(self._port, self._baud, timeout=self._timeout)
        self._ser.reset_input_buffer()
        self._ser.write(INIT_PACKET)
        time.sleep(_INTER_CMD_DELAY)

    def disconnect(self) -> None:
        if self._ser and self._ser.is_open:
            close_pkt = bytes([0xF1, 0xC1, 0x00, 0x01, 0x00, 0x01])
            self._ser.write(close_pkt)
            self._ser.flush()
            self._ser.close()
        self._ser = None

    @property
    def is_connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def _send(self, cmd: int, register: int, payload: bytes) -> None:
        assert self._ser, "Not connected"
        self._ser.write(build_packet(cmd, register, payload))
        time.sleep(_INTER_CMD_DELAY)

    def _read_response(self) -> tuple[int, bytes] | None:
        assert self._ser, "Not connected"
        for _ in range(256):
            b = self._ser.read(1)
            if not b:
                return None
            if b[0] == 0xF0:
                rest = self._ser.read(3)  # cmd, register, length
                if len(rest) < 3:
                    continue
                length = rest[2]
                body = self._ser.read(length + 1)  # payload + checksum
                result = _parse_response(bytes([0xF0]) + rest + body)
                if result is not None:
                    return result
                # bad frame — keep scanning
        return None

    def set_voltage(self, volts: float) -> None:
        self._send(0xB1, 0xC1, struct.pack('<f', volts))

    def set_current(self, amps: float) -> None:
        self._send(0xB1, 0xC2, struct.pack('<f', amps))

    def enable_output(self, on: bool) -> None:
        self._send(0xB1, 0xDB, bytes([0x01 if on else 0x00]))

    def get_measurements(self) -> dict:
        self._send(0xA1, 0xC3, b'')
        result = self._read_response()
        if result is None:
            raise IOError("No response from DPS-150")
        _, payload = result
        v, i, p = struct.unpack_from('<fff', payload)
        return {'voltage': round(v, 4), 'current': round(i, 4), 'power': round(p, 4)}

    def get_all_state(self) -> dict:
        self._send(0xA1, 0xFF, b'')
        result = self._read_response()
        if result is None:
            raise IOError("No response from DPS-150")
        _, p = result
        if len(p) < 119:
            raise IOError(f"get_all_state: short payload ({len(p)} bytes, expected ≥119)")
        return {
            'input_voltage':  struct.unpack_from('<f', p, 0)[0],
            'voltage_set':    struct.unpack_from('<f', p, 4)[0],
            'current_set':    struct.unpack_from('<f', p, 8)[0],
            'voltage':        struct.unpack_from('<f', p, 12)[0],
            'current':        struct.unpack_from('<f', p, 16)[0],
            'power':          struct.unpack_from('<f', p, 20)[0],
            'temperature':    struct.unpack_from('<f', p, 24)[0],
            'output_enabled': bool(p[107]),
            'protection':     p[108],
            'mode':           'CC' if p[109] == 0 else 'CV',
            'voltage_max':    struct.unpack_from('<f', p, 111)[0],
            'current_max':    struct.unpack_from('<f', p, 115)[0],
        }
