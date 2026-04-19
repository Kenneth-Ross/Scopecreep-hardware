# python/agent/config.py
import os

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
AGENT_MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "4096"))
AGENT_MAX_TOOL_ROUNDS = int(os.getenv("AGENT_MAX_TOOL_ROUNDS", "30"))
SCOPE_BACKEND = os.getenv("SCOPE_BACKEND", "waveforms")
SCOPE_BITSTREAM = os.getenv("SCOPE_BITSTREAM", "")
SCOPE_URL = os.getenv("SCOPE_URL", "ftdi://0x0403:0x6014/1")
MAX_VOLTAGE = float(os.getenv("MAX_VOLTAGE", "5.0"))
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "3600"))

# Back-compat alias: legacy callers may still import AGENT_MODEL.
AGENT_MODEL = OPENAI_MODEL
