# python/agent/config.py
import os

AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-4-6")
AGENT_MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "4096"))
AGENT_MAX_TOOL_ROUNDS = int(os.getenv("AGENT_MAX_TOOL_ROUNDS", "30"))
SCOPE_BITSTREAM = os.getenv("SCOPE_BITSTREAM", "")
SCOPE_URL = os.getenv("SCOPE_URL", "ftdi://0x0403:0x6014/1")
# Analog Discovery V+/V− rails are hardware-limited to ±5 V; override for external PSUs
MAX_VOLTAGE = float(os.getenv("MAX_VOLTAGE", "5.0"))
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
