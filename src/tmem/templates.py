from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Iterable, Mapping

from .shells import PS_SINGLE_QUOTES, active_shell, quote_argument

PARAMETER_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_-]*)\}\}")
# Operators are shell syntax, not replaceable argument text. Keeping them out of
# token spans preserves attached separators such as `value;` and `left&&right`.
TOKEN_RE = re.compile(r'''(?:[^\s'"\\;&|<>()]+|\\.|'(?:[^']*)'|"(?:\\.|[^"])*")+''')


@dataclass(frozen=True, slots=True)
class ShellToken:
    start: int
    end: int
    raw: str
    value: str


def parameter_names(templates: Iterable[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for template in templates:
        for match in PARAMETER_RE.finditer(template):
            name = match.group(1)
            if name not in seen:
                names.append(name)
                seen.add(name)
    return names


def render_template(template: str, values: Mapping[str, str], *, shell: str | None = None) -> str:
    shell = shell or active_shell()
    _validate_placeholder_context(template, shell)
    def replacement(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise KeyError(f"Missing value for parameter: {name}")
        return quote_argument(values[name], shell)

    return PARAMETER_RE.sub(replacement, template)


def shell_tokens(command: str, *, shell: str | None = None) -> list[ShellToken]:
    if (shell or active_shell()) == "powershell":
        return _powershell_tokens(command)
    result: list[ShellToken] = []
    for match in TOKEN_RE.finditer(command):
        raw = match.group(0)
        try:
            parsed = shlex.split(raw, posix=True)
            value = parsed[0] if len(parsed) == 1 else raw
        except ValueError:
            value = raw
        result.append(ShellToken(match.start(), match.end(), raw, value))
    return result


def apply_parameterization(
    command: str,
    replacements: Iterable[tuple[int, int, str]],
) -> str:
    result = command
    ordered = sorted(replacements, key=lambda item: item[0], reverse=True)
    previous_start = len(command) + 1
    for start, end, name in ordered:
        if start < 0 or end > len(command) or start >= end:
            raise ValueError("Invalid replacement span")
        if end > previous_start:
            raise ValueError("Overlapping replacement spans")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
            raise ValueError(f"Invalid parameter name: {name}")
        result = result[:start] + "{{" + name + "}}" + result[end:]
        previous_start = start
    return result


def build_script(commands: list[str], stop_on_error: bool = True, *, shell: str | None = None) -> str:
    if not commands:
        return ""
    if len(commands) == 1:
        return commands[0]
    if not stop_on_error:
        return "\n".join(commands)

    # Braces keep cd/source/export and other shell-state changes in the caller's
    # shell while still making each group step conditional on the previous one.
    prefix = ". " if (shell or active_shell()) == "powershell" else ""
    wrapped = [prefix + "{\n" + command + "\n}" for command in commands]
    return " &&\n".join(wrapped)


def _validate_placeholder_context(template: str, shell: str) -> None:
    """Substitutions must not be inside another quote or an escape sequence.

    Both shells would interpret literal quoting differently inside double quotes,
    potentially turning data back into executable substitutions. The UI replaces
    whole tokens; reject ambiguous hand-written templates instead of guessing.
    """
    quote = None
    escaped = False
    starts = {match.start() for match in PARAMETER_RE.finditer(template)}
    escape = "`" if shell == "powershell" else "\\"
    for index, char in enumerate(template):
        if shell == "powershell":
            if char in PS_SINGLE_QUOTES:
                char = "'"
            elif char in "\u201c\u201d\u201e":
                char = '"'
        if index in starts and (quote is not None or escaped):
            raise ValueError("Parameter placeholders must be outside quotes and escapes")
        if escaped:
            escaped = False
        elif char == escape and quote != "'":
            escaped = True
        elif char in {"'", '\"'}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char


# PowerShell uses doubled single quotes and backtick escapes, not POSIX escapes.
# Dynamic expressions are deliberately not offered as literal parameter values.
PS_TOKEN_RE = re.compile(r"(?:[^\s'\"`;&|<>(){}]+|`[\s\S]|'(?:[^']|'')*'|\"(?:`[\s\S]|[^\"])*\")+")


def _powershell_tokens(command: str) -> list[ShellToken]:
    result = []
    for match in PS_TOKEN_RE.finditer(command):
        raw = match.group(0)
        if raw.startswith("'") and raw.endswith("'"):
            value = re.sub("([" + re.escape(PS_SINGLE_QUOTES) + "])\\1", r"\1", raw[1:-1])
        elif "$" in raw or raw.startswith("@"):
            continue
        else:
            value = raw[1:-1] if raw.startswith('"') and raw.endswith('"') else raw
            value = re.sub(r"`(.)", r"\1", value)
        result.append(ShellToken(match.start(), match.end(), raw, value))
    return result
