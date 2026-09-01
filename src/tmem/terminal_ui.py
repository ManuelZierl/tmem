from __future__ import annotations

import base64
import contextlib
import os
import readline
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable, Optional


class FzfError(RuntimeError):
    pass


class MissingFzfError(FzfError):
    pass


@contextlib.contextmanager
def alternate_screen():
    try:
        tty = open("/dev/tty", "w", encoding="utf-8", buffering=1)
    except OSError:
        tty = None
    if tty is None:
        yield
        return
    try:
        tty.write("\x1b[?1049h\x1b[H\x1b[2J")
        tty.flush()
        yield
    finally:
        tty.write("\x1b[?1049l")
        tty.flush()
        tty.close()


def _open_tty() -> Optional[int]:
    try:
        return os.open("/dev/tty", os.O_RDWR)
    except OSError:
        return None


@contextlib.contextmanager
def _standard_streams_on_tty(tty_fd: int):
    """Temporarily give input() real terminal file descriptors and Readline."""
    sys.stdout.flush()
    saved_stdin = os.dup(sys.stdin.fileno())
    saved_stdout = os.dup(sys.stdout.fileno())
    try:
        os.dup2(tty_fd, sys.stdin.fileno())
        os.dup2(tty_fd, sys.stdout.fileno())
        yield
    finally:
        sys.stdout.flush()
        os.dup2(saved_stdin, sys.stdin.fileno())
        os.dup2(saved_stdout, sys.stdout.fileno())
        os.close(saved_stdin)
        os.close(saved_stdout)


def run_on_terminal(command: list[str]) -> subprocess.CompletedProcess[str]:
    tty_fd = _open_tty()
    if tty_fd is None:
        raise OSError("A controlling terminal is required to open an editor")
    try:
        return subprocess.run(
            command,
            stdin=tty_fd,
            stdout=tty_fd,
            stderr=tty_fd,
            text=True,
            check=False,
        )
    finally:
        os.close(tty_fd)


@dataclass(slots=True)
class FzfResult:
    key: str
    rows: list[str]


def run_fzf(
    rows: Iterable[str],
    *,
    header: str,
    prompt: str = "> ",
    multi: bool = False,
    expect: tuple[str, ...] = ("enter", "right", "left"),
    no_sort: bool = False,
    query: Optional[str] = None,
) -> Optional[FzfResult]:
    executable = shutil.which("fzf")
    if executable is None:
        raise MissingFzfError(
            "fzf is required for the interactive UI. Install it on Ubuntu with: sudo apt install fzf"
        )

    command = [
        executable,
        "--delimiter=\t",
        "--with-nth=2..",
        "--layout=reverse",
        "--border=rounded",
        "--info=inline",
        f"--prompt={prompt}",
        f"--header={header}",
        "--header-first",
        "--cycle",
        "--no-mouse",
    ]
    if expect:
        command.append("--expect=" + ",".join(expect))
    if multi:
        command.extend(["--multi", "--bind=tab:toggle+down,btab:toggle+up"])
    else:
        command.append("--no-multi")
    if no_sort:
        command.append("--no-sort")
    if query:
        command.append(f"--query={query}")

    input_text = "\n".join(rows)
    if input_text:
        input_text += "\n"

    with alternate_screen():
        process = subprocess.run(
            command,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=None,
            check=False,
        )

    if process.returncode in (1, 130) or not process.stdout:
        return None
    if process.returncode != 0:
        raise FzfError(f"fzf exited with status {process.returncode}")

    output = process.stdout.splitlines()
    if expect:
        key = output[0] if output else ""
        selected = output[1:]
    else:
        key = "enter"
        selected = output
    if not selected:
        return None
    return FzfResult(key=key or "enter", rows=selected)


def prompt_text(
    title: str,
    prompt: str,
    *,
    default: Optional[str] = None,
    details: Optional[list[str]] = None,
    allow_empty: bool = True,
) -> Optional[str]:
    tty_fd = _open_tty()
    if tty_fd is None:
        rendered = f"{prompt} [{default}] " if default is not None else f"{prompt} "
        print(title, file=sys.stderr)
        print(rendered, end="", file=sys.stderr, flush=True)
        try:
            value = input()
        except (EOFError, KeyboardInterrupt):
            return None
        value = value if value else (default or "")
        return value if allow_empty or value else None

    try:
        with _standard_streams_on_tty(tty_fd):
            print("\x1b[?1049h\x1b[H\x1b[2J", end="")
            print(title)
            print("=" * max(8, min(len(title), 72)), end="\n\n")
            for line in details or []:
                print(line)
            if details:
                print()
            rendered = f"{prompt} [{default}]: " if default is not None else f"{prompt}: "
            try:
                value = input(rendered)
            except (EOFError, KeyboardInterrupt):
                return None
            if not value and default is not None:
                value = default
            if not allow_empty and not value:
                return None
            return value
    except KeyboardInterrupt:
        return None
    finally:
        os.write(tty_fd, b"\x1b[?1049l")
        os.close(tty_fd)


def confirm(title: str, question: str, *, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    answer = prompt_text(title, f"{question} [{suffix}]", allow_empty=True)
    if answer is None or not answer.strip():
        return default
    return answer.strip().casefold() in {"y", "yes"}


def show_text(title: str, lines: list[str]) -> None:
    tty_fd = _open_tty()
    if tty_fd is None:
        print(title, file=sys.stderr)
        for line in lines:
            print(line, file=sys.stderr)
        return
    os.close(tty_fd)
    display_lines = [part for line in lines for part in (line.splitlines() or [""])]
    rows = [f"line:{index}\t{line}" for index, line in enumerate(display_lines)] or ["line:0\t(no data)"]
    try:
        run_fzf(
            rows,
            header=f"{title}\nEnter or ← to return",
            prompt="",
            multi=False,
            expect=("enter", "left"),
            no_sort=True,
        )
    except FzfError:
        print(title, file=sys.stderr)
        for line in lines:
            print(line, file=sys.stderr)


def encode_hidden(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")


def decode_hidden(value: str) -> str:
    return base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")
