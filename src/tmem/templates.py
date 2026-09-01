from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Iterable, Mapping

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


def render_template(template: str, values: Mapping[str, str]) -> str:
    def replacement(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise KeyError(f"Missing value for parameter: {name}")
        return shlex.quote(values[name])

    return PARAMETER_RE.sub(replacement, template)


def shell_tokens(command: str) -> list[ShellToken]:
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


def build_script(commands: list[str], stop_on_error: bool = True) -> str:
    if not commands:
        return ""
    if len(commands) == 1:
        return commands[0]
    if not stop_on_error:
        return "\n".join(commands)

    # Braces keep cd/source/export and other shell-state changes in the caller's
    # shell while still making each group step conditional on the previous one.
    wrapped = ["{\n" + command + "\n}" for command in commands]
    return " &&\n".join(wrapped)
