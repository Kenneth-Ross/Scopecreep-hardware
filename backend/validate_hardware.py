#!/usr/bin/env python3
"""
Manual multimeter validation script for FNIRSI DPS-150.
Runs on macOS with DPS-150 plugged in via USB.

Usage:
    python validate_hardware.py /dev/tty.usbmodemXXXX
"""

import sys
import struct
import time
import serial

BAUD = 115200
TIMEOUT = 0.5
INTER_CMD_DELAY = 0.05

INIT_PACKET = bytes([0xF1, 0xC1, 0x00, 0x01, 0x01, 0x02])
CLOSE_PACKET = bytes([0xF1, 0xC1, 0x00, 0x01, 0x00, 0x01])

STEPS = [
    (1.0, 0.2),
    (2.0, 0.2),
    (3.0, 0.2),
    (4.0, 0.2),
    (5.0, 0.2),
]


def build_packet(cmd, register, payload):
    length = len(payload)
    checksum = (register + length + sum(payload)) % 256
    return bytes([0xF1, cmd, register, length]) + payload + bytes([checksum])


def send(ser, cmd, register, payload=b''):
    ser.write(build_packet(cmd, register, payload))
    time.sleep(INTER_CMD_DELAY)


def set_voltage(ser, v):
    send(ser, 0xB1, 0xC1, struct.pack('<f', v))

def set_current(ser, a):
    send(ser, 0xB1, 0xC2, struct.pack('<f', a))

def enable_output(ser, on):
    send(ser, 0xB1, 0xDB, bytes([0x01 if on else 0x00]))


def prompt(msg):
    input(f"\n  {msg}\n  Press Enter when ready... ")


def hr():
    print("  " + "-" * 50)


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_hardware.py /dev/tty.usbmodemXXXX")
        print("\nFind your port with:  ls /dev/tty.usbmodem* /dev/cu.*")
        sys.exit(1)

    port = sys.argv[1]

    print(f"\n  DPS-150 Multimeter Validation")
    hr()
    print(f"  Port   : {port}")
    print(f"  Steps  : {', '.join(f'{v}V/{int(a*1000)}mA' for v,a in STEPS)}")
    print(f"  Mode   : manual eyeball (closed circuit)")
    hr()
    prompt("Connect multimeter probes to DPS-150 output terminals, then press Enter to connect")

    try:
        ser = serial.Serial(port, BAUD, timeout=TIMEOUT, rtscts=True)
        ser.reset_input_buffer()
        ser.write(INIT_PACKET)
        time.sleep(INTER_CMD_DELAY)
        print(f"\n  Connected to {port}")
    except serial.SerialException as e:
        print(f"\n  ERROR: Cannot open port: {e}")
        sys.exit(1)

    passed = []
    failed = []

    try:
        for step_num, (volts, amps) in enumerate(STEPS, 1):
            print(f"\n  Step {step_num}/{len(STEPS)}: {volts}V / {int(amps*1000)}mA")
            hr()

            set_voltage(ser, volts)
            set_current(ser, amps)
            enable_output(ser, True)
            print(f"  Output ON — set {volts}V, {int(amps*1000)}mA limit")

            prompt(f"Set multimeter to DC VOLTAGE. Expected: {volts}V")
            v_ok = input("  Voltage looks good? [y/n]: ").strip().lower()

            prompt(f"Set multimeter to DC CURRENT. Expected: ≤{int(amps*1000)}mA")
            a_ok = input("  Current looks good? [y/n]: ").strip().lower()

            enable_output(ser, False)
            print("  Output OFF")

            result = "PASS" if v_ok == 'y' and a_ok == 'y' else "FAIL"
            (passed if result == "PASS" else failed).append(f"{volts}V/{int(amps*1000)}mA")
            print(f"  Result: {result}")

    finally:
        enable_output(ser, False)
        ser.write(CLOSE_PACKET)
        ser.flush()
        ser.close()

    print(f"\n  Validation complete")
    hr()
    print(f"  PASSED : {len(passed)} — {', '.join(passed) if passed else 'none'}")
    print(f"  FAILED : {len(failed)} — {', '.join(failed) if failed else 'none'}")
    hr()


if __name__ == "__main__":
    main()
