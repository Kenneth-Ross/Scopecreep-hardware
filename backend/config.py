import os

PSU_PORT = os.getenv("PSU_PORT", "/dev/ttyACM0")
PSU_BAUD = int(os.getenv("PSU_BAUD", "115200"))
PSU_TIMEOUT = float(os.getenv("PSU_TIMEOUT", "0.5"))
