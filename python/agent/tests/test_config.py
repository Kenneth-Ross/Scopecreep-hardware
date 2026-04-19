def test_config_imports():
    from agent.config import (
        AGENT_MODEL, AGENT_MAX_TOKENS, AGENT_MAX_TOOL_ROUNDS,
        SCOPE_BITSTREAM, SCOPE_URL, MAX_VOLTAGE,
        SESSION_TTL_SECONDS,
    )
    assert isinstance(AGENT_MODEL, str)
    assert AGENT_MAX_TOKENS > 0
    assert AGENT_MAX_TOOL_ROUNDS > 0
    assert MAX_VOLTAGE > 0
    assert SESSION_TTL_SECONDS > 0
