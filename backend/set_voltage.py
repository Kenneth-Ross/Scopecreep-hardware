#!/usr/bin/env python3
"""
Set voltage and current on DPS-150, enable output, read back measurements.
Usage: python3 set_voltage.py /dev/cu.usbmodemXXXX <voltage> <current_limit>
Example: python3 set_voltage.py /dev/cu.usbmodem1480DFDC40251 5.0 0.2
"""

import sys
import struct
import serial
import time

PORT    = sys.argv[1] if len(sys.argv) > 1 else '/dev/cu.usbmodem1480DFDC40251'
VOLTS   = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
AMPS    = float(sys.argv[3]) if len(sys.argv) > 3 else 0.2

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

print(f"Set {VOLTS}V / {AMPS}A — output ON")
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
