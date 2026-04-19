# python/executor/prompts.py
from __future__ import annotations

import sys
from typing import TextIO


def wait_enter(msg: str, stdin: TextIO | None = None) -> None:
    stdin = stdin or sys.stdin
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()
    stdin.readline()


def confirm(msg: str, stdin: TextIO | None = None, default_yes: bool = False) -> bool:
    stdin = stdin or sys.stdin
    suffix = " [Y/n]" if default_yes else " [y/N]"
    # Trailing newline so line-buffered readers (e.g. the IDE plugin's
    # TestFlowPanel) see the prompt immediately instead of waiting for
    # the user's response to flush the partial line out of the buffer.
    sys.stdout.write(msg + suffix + "\n")
    sys.stdout.flush()
    line = stdin.readline().strip().lower()
    if not line:
        return default_yes
    return line in ("y", "yes")
