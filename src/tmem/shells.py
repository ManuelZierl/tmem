"""The active command language is explicit; an OS is not a shell."""
from __future__ import annotations

import os
import shlex

SHELLS = ("bash", "zsh", "powershell")
# PowerShell treats these Unicode characters as single-quote delimiters too.
PS_SINGLE_QUOTES = "\'\u2018\u2019\u201a\u201b"


def active_shell() -> str:
    shell = os.environ.get("TMEM_SHELL", "bash").lower()
    if shell not in SHELLS:
        raise ValueError(f"Unsupported shell: {shell}; expected {', '.join(SHELLS)}")
    return shell


def compatible_shell(source: str, target: str) -> bool:
    # Both Unix adapters use POSIX-style quoting. This is not syntax translation:
    # commands using Bash/Zsh-specific builtins still require the original shell.
    return source == target or {source, target} <= {"bash", "zsh"}


def quote_argument(value: str, shell: str | None = None) -> str:
    shell = shell or active_shell()
    if "\0" in value:
        raise ValueError("Shell arguments cannot contain NUL characters")
    if shell == "powershell":
        return "'" + "".join(char * 2 if char in PS_SINGLE_QUOTES else char for char in value) + "'"
    if shell in {"bash", "zsh"}:
        return shlex.quote(value)
    raise ValueError(f"Unsupported shell: {shell}")
