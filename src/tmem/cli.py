from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from . import __version__
from .shells import active_shell, SHELLS
from .config import db_path, load_config
from .db import TmemDB, current_scope_cwd, now_ms
from .fuzzy import fuzzy_filter
from .models import HistoryEntry, Memory
from .terminal_ui import FzfError, confirm
from .templates import parameter_names
from .tui import Execution, TmemUI


def _protocol(execution: Execution) -> str:
    if "\0" in execution.script:
        raise ValueError("Shell commands cannot contain NUL characters")
    script = base64.b64encode(execution.script.encode("utf-8")).decode("ascii")
    display = base64.b64encode(execution.display.encode("utf-8")).decode("ascii")
    memory_id = str(execution.memory_id or "")
    return f"execute\t{script}\t{display}\t{memory_id}"


def _parse_overrides(values: Sequence[str], parameter_order: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    positional_index = 0
    for value in values:
        name, separator, raw = value.partition("=")
        if separator and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
            if name in result:
                raise ValueError(f"Parameter supplied more than once: {name}")
            result[name] = raw
            continue

        while positional_index < len(parameter_order) and parameter_order[positional_index] in result:
            positional_index += 1
        if positional_index >= len(parameter_order):
            raise ValueError(f"Too many positional parameter values; unexpected: {value}")
        result[parameter_order[positional_index]] = value
        positional_index += 1
    return result


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def _history_text(entry: HistoryEntry) -> str:
    return f"{entry.command}\n{entry.cwd}"


def _find_memory(db: TmemDB, name: str, *, global_only: bool = False) -> Optional[Memory]:
    if global_only:
        return db.get_memory(name)
    return db.resolve_memory(name, current_scope_cwd())


def _format_history(entries: list[HistoryEntry], as_json: bool = False) -> None:
    if as_json:
        print(
            json.dumps(
                [
                    {
                        "id": item.id,
                        "command": item.command,
                        "cwd": item.cwd,
                        "exit_code": item.exit_code,
                        "started_at_ms": item.started_at_ms,
                        "finished_at_ms": item.finished_at_ms,
                        "duration_ms": item.duration_ms,
                        "hostname": item.hostname,
                        "session_id": item.session_id,
                        "shell": item.shell,
                    }
                    for item in entries
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    for item in entries:
        stamp = datetime.fromtimestamp(item.finished_at_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
        status = "?" if item.exit_code is None else str(item.exit_code)
        cwd = item.cwd or "—"
        command = " ↵ ".join(part.strip() for part in item.command.splitlines())
        print(f"{stamp}  exit={status:<3}  {cwd}  {command}")


def _query_history(
    db: TmemDB,
    query: str,
    *,
    limit: int,
    cwd: Optional[str] = None,
    failed: bool = False,
    since_ms: Optional[int] = None,
    candidate_limit: int = 50000,
) -> list[HistoryEntry]:
    # Rank a large recent candidate pool while keeping startup bounded. The
    # database itself is never pruned by this limit.
    pool_limit = max(limit, candidate_limit)
    entries = db.list_history(
        limit=pool_limit,
        cwd=cwd,
        failed_only=failed,
        since_ms=since_ms,
    )
    if not query.strip():
        return entries[:limit]
    return fuzzy_filter(query, entries, _history_text, limit=limit)


def _print_memory(memory: Memory, as_json: bool = False) -> None:
    payload = {
        "id": memory.id,
        "name": memory.name,
        "description": memory.description,
        "stop_on_error": memory.stop_on_error,
        "run_count": memory.run_count,
        "last_run_at_ms": memory.last_run_at_ms,
        "commands": [step.command_template for step in memory.steps],
        "directory": memory.scope_cwd or None,
        "shell": memory.shell,
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    kind = "group" if memory.is_group else "command"
    print(f"{memory.name} ({kind})")
    if memory.description:
        print(memory.description)
    print(f"Runs: {memory.run_count}")
    print(f"Failure mode: {'stop' if memory.stop_on_error else 'continue'}")
    print(f"Directory: {memory.scope_cwd or 'global'}")
    for index, step in enumerate(memory.steps, start=1):
        prefix = f"{index}. " if memory.is_group else ""
        print(prefix + step.command_template)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmem",
        description="Persistent, fuzzy-searchable terminal command memory.",
    )
    parser.add_argument("--version", action="version", version=f"tmem {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("clock", help=argparse.SUPPRESS)
    decode = sub.add_parser("shell-decode", help=argparse.SUPPRESS)
    decode.add_argument("value")
    init = sub.add_parser("init", help="Print a shell integration for your profile")
    init.add_argument("shell", choices=SHELLS)
    sub.add_parser("shell-ui", help="Internal: open the TUI and emit a shell response")
    shell_run = sub.add_parser("shell-run", help="Internal: resolve a memory for the shell")
    shell_run.add_argument("name")
    shell_run.add_argument("parameters", nargs="*")
    shell_run.add_argument("--defaults", action="store_true")
    shell_run.add_argument("--global", dest="global_only", action="store_true")

    record = sub.add_parser("record", help="Internal: record one completed command")
    record.add_argument("--cwd", required=True)
    record.add_argument("--exit-code", type=int)
    record.add_argument("--started-at-ms", type=int)
    record.add_argument("--finished-at-ms", type=int)
    record.add_argument("--hostname", default=socket.gethostname())
    record.add_argument("--session", default="")
    record.add_argument("--shell", default="bash")

    exists = sub.add_parser("memory-exists", help="Internal: test whether a memory exists")
    exists.add_argument("name")

    note_run = sub.add_parser("note-run", help="Internal: update memory usage after execution")
    note_run.add_argument("memory_id", type=int)

    search = sub.add_parser("search", help="Fuzzy-search history non-interactively")
    search.add_argument("query", nargs="*", help="Fuzzy query")
    search.add_argument("--limit", type=_non_negative_int, default=50)
    search.add_argument("--cwd")
    search.add_argument("--failed", action="store_true")
    search.add_argument("--json", action="store_true")

    failed = sub.add_parser("failed", help="Show failed commands")
    failed.add_argument("query", nargs="*")
    failed.add_argument("--limit", type=_non_negative_int, default=50)
    failed.add_argument("--json", action="store_true")

    today = sub.add_parser("today", help="Show commands run today")
    today.add_argument("query", nargs="*")
    today.add_argument("--limit", type=_non_negative_int, default=100)
    today.add_argument("--json", action="store_true")

    cwd = sub.add_parser("cwd", help="Show commands run in the current directory")
    cwd.add_argument("query", nargs="*")
    cwd.add_argument("--limit", type=_non_negative_int, default=100)
    cwd.add_argument("--json", action="store_true")

    sub.add_parser("list", help="List saved memories")

    show = sub.add_parser("show", help="Show a saved memory")
    show.add_argument("name")
    show.add_argument("--json", action="store_true")
    show.add_argument("--global", dest="global_only", action="store_true")

    edit = sub.add_parser("edit", help="Edit a memory in $EDITOR")
    edit.add_argument("name")
    edit.add_argument("--global", dest="global_only", action="store_true")

    remove = sub.add_parser("rm", aliases=["remove"], help="Delete a saved memory")
    remove.add_argument("name")
    remove.add_argument("--yes", action="store_true")
    remove.add_argument("--global", dest="global_only", action="store_true")

    save = sub.add_parser("save", help="Save a command without opening the TUI")
    save.add_argument("name")
    save.add_argument("--description", default="")
    save.add_argument("--here", action="store_true", help="Bind the memory to this directory")
    save.add_argument("command_text", nargs=argparse.REMAINDER)

    group = sub.add_parser("group", help="Save a command group; separate steps with :::")
    group.add_argument("name")
    group.add_argument("--description", default="")
    group.add_argument("--here", action="store_true", help="Bind the memory to this directory")
    group.add_argument("command_text", nargs=argparse.REMAINDER)

    stats = sub.add_parser("stats", help="Show basic command-history statistics")
    stats.add_argument("--limit", type=_non_negative_int, default=15)

    importer = sub.add_parser("import-history", help="Import Bash, zsh, or PSReadLine history")
    importer.add_argument("path", nargs="?")
    importer.add_argument("--shell", choices=SHELLS)

    sub.add_parser("doctor", help="Check the installation")
    return parser


def _split_group_arguments(values: list[str]) -> list[str]:
    if values and values[0] == "--":
        values = values[1:]
    steps: list[list[str]] = [[]]
    for value in values:
        if value == ":::":
            steps.append([])
        else:
            steps[-1].append(value)
    commands = [" ".join(step).strip() for step in steps]
    return [command for command in commands if command]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        # Native PowerShell pipes and fzf use UTF-8, irrespective of Windows ACP.
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure") and not stream.isatty():
                stream.reconfigure(encoding="utf-8")
        if args.command == "clock":
            print(now_ms())
            return 0
        if args.command == "shell-decode":
            print(base64.b64decode(args.value, validate=True).decode("utf-8"), end="")
            return 0
        if args.command == "init":
            suffix = "ps1" if args.shell == "powershell" else args.shell
            directory = Path(__file__).parent / "shell"
            if not directory.is_dir():  # editable/source checkout
                directory = Path(__file__).parents[2] / "shell"
            print((directory / f"tmem.{suffix}").read_text(encoding="utf-8"), end="")
            return 0
        config = load_config()
        with TmemDB(db_path()) as db:
            if args.command in (None, "shell-ui"):
                try:
                    execution = TmemUI(db, config.history_limit).run()
                except FzfError as error:
                    print(error, file=sys.stderr)
                    return 2
                if execution is not None:
                    print(_protocol(execution))
                return 0

            if args.command == "shell-run":
                memory = _find_memory(db, args.name, global_only=args.global_only)
                if memory is None:
                    print(f"Unknown memory: {args.name}", file=sys.stderr)
                    return 1
                try:
                    definitions = db.parameter_definitions(memory.id)
                    overrides = _parse_overrides(
                        args.parameters, [definition.name for definition in definitions]
                    )
                    execution = TmemUI(db, config.history_limit).resolve_memory(
                        memory,
                        overrides=overrides,
                        use_defaults_without_prompt=args.defaults,
                    )
                except (ValueError, FzfError) as error:
                    print(error, file=sys.stderr)
                    return 2
                if execution is not None:
                    print(_protocol(execution))
                return 0

            if args.command == "record":
                command = sys.stdin.read().rstrip("\n")
                if not command.strip():
                    return 0
                if any(re.search(pattern, command) for pattern in config.ignore_patterns):
                    return 0
                db.record_history(
                    command=command,
                    cwd=args.cwd,
                    exit_code=args.exit_code,
                    started_at_ms=args.started_at_ms,
                    finished_at_ms=now_ms() if args.finished_at_ms is None else args.finished_at_ms,
                    hostname=args.hostname,
                    session_id=args.session,
                    shell=args.shell,
                )
                return 0

            if args.command == "memory-exists":
                return 0 if db.resolve_memory(args.name, current_scope_cwd()) is not None else 1

            if args.command == "note-run":
                db.mark_memory_run(args.memory_id)
                return 0

            if args.command == "search":
                entries = _query_history(
                    db,
                    " ".join(args.query),
                    limit=args.limit,
                    cwd=args.cwd,
                    failed=args.failed,
                    candidate_limit=config.history_limit,
                )
                _format_history(entries, args.json)
                return 0

            if args.command == "failed":
                entries = _query_history(
                    db,
                    " ".join(args.query),
                    limit=args.limit,
                    failed=True,
                    candidate_limit=config.history_limit,
                )
                _format_history(entries, args.json)
                return 0

            if args.command == "today":
                start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                entries = _query_history(
                    db,
                    " ".join(args.query),
                    limit=args.limit,
                    since_ms=int(start.timestamp() * 1000),
                    candidate_limit=config.history_limit,
                )
                _format_history(entries, args.json)
                return 0

            if args.command == "cwd":
                entries = _query_history(
                    db,
                    " ".join(args.query),
                    limit=args.limit,
                    cwd=os.getcwd(),
                    candidate_limit=config.history_limit,
                )
                _format_history(entries, args.json)
                return 0

            if args.command == "list":
                memories = db.list_memories()
                for memory in memories:
                    kind = "group" if memory.is_group else "command"
                    parameters = parameter_names(step.command_template for step in memory.steps)
                    suffix = f"  params={','.join(parameters)}" if parameters else ""
                    scope = "global" if memory.is_global else memory.scope_cwd
                    print(
                        f"{memory.name:<24} {kind:<7} {len(memory.steps)} step(s)"
                        f"  scope={scope}{suffix}"
                    )
                return 0

            if args.command == "show":
                memory = _find_memory(db, args.name, global_only=args.global_only)
                if memory is None:
                    print(f"Unknown memory: {args.name}", file=sys.stderr)
                    return 1
                _print_memory(memory, args.json)
                return 0

            if args.command == "edit":
                memory = _find_memory(db, args.name, global_only=args.global_only)
                if memory is None:
                    print(f"Unknown memory: {args.name}", file=sys.stderr)
                    return 1
                updated = TmemUI(db, config.history_limit)._edit_memory(memory)
                return 0 if updated is not None else 1

            if args.command in ("rm", "remove"):
                memory = _find_memory(db, args.name, global_only=args.global_only)
                if memory is None:
                    print(f"Unknown memory: {args.name}", file=sys.stderr)
                    return 1
                scope = memory.scope_cwd or "global"
                if not args.yes and not confirm(
                    "Delete memory", f"Delete {memory.name!r} from {scope}?"
                ):
                    return 1
                db.delete_memory(memory.id)
                return 0

            if args.command == "save":
                command = " ".join(args.command_text[1:] if args.command_text[:1] == ["--"] else args.command_text)
                if not command.strip():
                    print("Provide a command after --", file=sys.stderr)
                    return 2
                db.create_memory(
                    args.name,
                    [command],
                    description=args.description,
                    scope_cwd=current_scope_cwd() if args.here else "",
                )
                return 0

            if args.command == "group":
                commands = _split_group_arguments(args.command_text)
                if len(commands) < 2:
                    print("Provide at least two commands separated by :::", file=sys.stderr)
                    return 2
                db.create_memory(
                    args.name,
                    commands,
                    description=args.description,
                    scope_cwd=current_scope_cwd() if args.here else "",
                )
                return 0

            if args.command == "stats":
                total = db.history_count()
                successful, failed_count, unknown = db.history_status_counts()
                print(f"Recorded commands: {total}")
                print(f"Successful: {successful}")
                print(f"Failed: {failed_count}")
                print(f"Unknown status: {unknown}")
                if total:
                    print("\nMost frequent commands:")
                    for command, count in db.top_commands(args.limit):
                        compact = " ↵ ".join(command.splitlines())
                        print(f"{count:>5}  {compact}")
                return 0

            if args.command == "import-history":
                shell = args.shell or active_shell()
                defaults = {"bash": Path.home() / ".bash_history", "zsh": Path.home() / ".zsh_history"}
                default = os.environ.get("TMEM_HISTORY_FILE") or defaults.get(shell)
                if not args.path and default is None:
                    raise ValueError("Provide the PSReadLine history path or load the PowerShell integration")
                path = Path(args.path or default).expanduser()
                if not path.exists():
                    print(f"History file not found: {path}", file=sys.stderr)
                    return 1
                imported, skipped = db.import_history(path, shell)
                print(f"Imported {imported} commands; skipped {skipped} already imported entries.")
                return 0

            if args.command == "doctor":
                checks = {
                    "Python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                    "Database": str(db.path),
                    "fzf": shutil.which("fzf") or "missing",
                    "Shell integration": "loaded" if os.environ.get("TMEM_SHELL_INTEGRATION") == "1" else "not detected",
                    "Shell": active_shell(),
                    "Capture mode": os.environ.get("TMEM_CAPTURE_MODE", "not detected"),
                }
                failed_check = False
                for name, value in checks.items():
                    okay = value not in {"missing", "not detected"}
                    failed_check = failed_check or not okay
                    print(f"{'OK' if okay else 'WARN':<4} {name}: {value}")
                return 1 if failed_check else 0

    except sqlite3.IntegrityError as error:
        print(f"Database constraint error: {error}", file=sys.stderr)
        return 1
    except sqlite3.Error as error:
        print(f"Database error: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1

    parser.print_help()
    return 2
