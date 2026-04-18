#!/usr/bin/env python3
"""
Set voltage and current limit on DPS-150, enable output, read back measurements.

Usage:
  python3 set_voltage.py <port> <voltage> <current_limit> [resistance_ohms]

Arguments:
  port             Serial port (e.g. /dev/cu.usbmodem1480DFDC40251)
  voltage          Output voltage in volts (e.g. 5.0)
  current_limit    Current limit in amps (e.g. 0.2 for 200mA)
  resistance_ohms  Optional: load resistance in ohms. If provided, expected
                   current is calculated and shown alongside measurements.

Examples:
  # Open circuit — validate voltage only
  python3 set_voltage.py /dev/cu.usbmodem1480DFDC40251 5.0 0.2

  # With 33 ohm resistor — expect ~150mA at 5V, limit won't trigger
  python3 set_voltage.py /dev/cu.usbmodem1480DFDC40251 5.0 0.2 33

  # Wire short (0 ohm) — expect current limit to clamp at 200mA (CC mode)
  python3 set_voltage.py /dev/cu.usbmodem1480DFDC40251 5.0 0.2 0
"""

import sys
import struct
import serial
import time

if len(sys.argv) < 4:
    print(__doc__)
    sys.exit(1)

PORT    = sys.argv[1]
VOLTS   = float(sys.argv[2])
AMPS    = float(sys.argv[3])
OHMS    = float(sys.argv[4]) if len(sys.argv) > 4 else None

BAUD    = 115200
DELAY   = 0.05


def build(cmd, register, payload=b''):
    length = len(payload)
    cksum = (register + length + sum(payload)) % 256
    return bytes([0xF1, cmd, register, length]) + payload + bytes([cksum])


def read_frames(ser, timeout=1.0):
    ser.timeout = timeout
    data = ser.read(512)
    frames = {}
    i = 0
    while i < len(data) - 4:
        if data[i] == 0xF0 and data[i+1] == 0xA1:
            reg = data[i+2]
            length = data[i+3]
            if i + 4 + length + 1 <= len(data):
                payload = data[i+4:i+4+length]
                frames[reg] = payload
                i += 4 + length + 1
                continue
        i += 1
    return frames


ser = serial.Serial(PORT, BAUD, timeout=1.0, rtscts=True)
ser.reset_input_buffer()

# init
ser.write(bytes([0xF1, 0xC1, 0x00, 0x01, 0x01, 0x02]))
time.sleep(DELAY)
ser.write(bytes([0xF1, 0xB0, 0x00, 0x01, 0x05, 0x06]))
time.sleep(DELAY)

# set voltage and current
ser.write(build(0xB1, 0xC1, struct.pack('<f', VOLTS)))
time.sleep(DELAY)
ser.write(build(0xB1, 0xC2, struct.pack('<f', AMPS)))
time.sleep(DELAY)

# enable output
ser.write(build(0xB1, 0xDB, bytes([0x01])))
time.sleep(DELAY)

if OHMS is not None:
    expected_i = min(VOLTS / OHMS, AMPS) if OHMS > 0 else AMPS
    expected_mode = "CC (current limited)" if OHMS == 0 or (VOLTS / OHMS) >= AMPS else "CV (voltage regulated)"
    print(f"Set {VOLTS}V / {AMPS}A limit — load {OHMS}Ω")
    print(f"Expected: {expected_i:.4f}A  mode: {expected_mode}")
else:
    print(f"Set {VOLTS}V / {AMPS}A limit — no load specified")
print("Reading measurements for 3 seconds...")

for _ in range(6):
    time.sleep(0.5)
    frames = read_frames(ser, timeout=0.1)
    if 0xC3 in frames:
        p = frames[0xC3]
        v, i, w = struct.unpack_from('<fff', p)
        print(f"  V={v:.3f}V  I={i:.4f}A  P={w:.4f}W")

input("\nPress Enter to disable output...")

ser.write(build(0xB1, 0xDB, bytes([0x00])))
time.sleep(DELAY)
print("Output OFF")

ser.close()
