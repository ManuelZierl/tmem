from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from .cli import main as cli_main
from .config import db_path
from .db import TmemDB, current_scope_cwd, normalize_scope_cwd
from .models import Memory
from .shells import SHELLS

FORMAT = "tmem-memories"
FORMAT_VERSION = 1
TRANSFER_COMMANDS = {"export", "import"}


def _memory_payload(db: TmemDB, memory: Memory) -> dict[str, Any]:
    defaults = {
        definition.name: definition.default_value
        for definition in db.parameter_definitions(memory.id)
        if definition.default_value is not None
    }
    scope: dict[str, str] = {"kind": "global"}
    if memory.scope_cwd:
        scope = {"kind": "directory", "path": memory.scope_cwd}
    return {
        "name": memory.name,
        "description": memory.description,
        "shell": memory.shell,
        "scope": scope,
        "stop_on_error": memory.stop_on_error,
        "commands": [step.command_template for step in memory.steps],
        "parameter_defaults": defaults,
    }


def _export_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmem export",
        description="Export saved memory definitions as portable, versioned JSON.",
    )
    parser.add_argument("names", nargs="*", help="Memory names; omit to export all memories")
    parser.add_argument("-o", "--output", help="Write to a file instead of stdout")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output file")
    return parser


def _import_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmem import",
        description="Import memory definitions previously produced by tmem export.",
    )
    parser.add_argument("path", nargs="?", default="-", help="JSON file, or - for stdin")
    parser.add_argument(
        "--scope",
        choices=("preserve", "global", "here"),
        default="preserve",
        help="How imported directory scopes are mapped on this machine",
    )
    parser.add_argument(
        "--on-conflict",
        choices=("error", "skip", "replace"),
        default="error",
        help="What to do when name and target scope already exist",
    )
    return parser


def _read_document(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _parse_memory(raw: Any, index: int, scope_mode: str) -> dict[str, Any]:
    prefix = f"memories[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{prefix} must be an object")

    name = _require_string(raw.get("name"), f"{prefix}.name")
    description = _require_string(raw.get("description", ""), f"{prefix}.description")
    shell = _require_string(raw.get("shell"), f"{prefix}.shell")
    if shell not in SHELLS:
        raise ValueError(f"{prefix}.shell is unsupported: {shell}")

    stop_on_error = raw.get("stop_on_error", True)
    if not isinstance(stop_on_error, bool):
        raise ValueError(f"{prefix}.stop_on_error must be a boolean")

    commands = raw.get("commands")
    if not isinstance(commands, list) or not commands or not all(isinstance(item, str) for item in commands):
        raise ValueError(f"{prefix}.commands must be a non-empty array of strings")

    defaults = raw.get("parameter_defaults", {})
    if not isinstance(defaults, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in defaults.items()
    ):
        raise ValueError(f"{prefix}.parameter_defaults must map strings to strings")

    if scope_mode == "global":
        scope_cwd = ""
    elif scope_mode == "here":
        scope_cwd = current_scope_cwd()
    else:
        scope = raw.get("scope", {"kind": "global"})
        if not isinstance(scope, dict):
            raise ValueError(f"{prefix}.scope must be an object")
        kind = scope.get("kind")
        if kind == "global":
            scope_cwd = ""
        elif kind == "directory":
            path = _require_string(scope.get("path"), f"{prefix}.scope.path")
            scope_cwd = normalize_scope_cwd(path)
        else:
            raise ValueError(f"{prefix}.scope.kind must be global or directory")

    return {
        "name": name,
        "description": description,
        "shell": shell,
        "scope_cwd": scope_cwd,
        "stop_on_error": stop_on_error,
        "commands": commands,
        "defaults": defaults,
    }


def _validated_import(document: Any, scope_mode: str) -> list[dict[str, Any]]:
    if not isinstance(document, dict):
        raise ValueError("Import file must contain a JSON object")
    if document.get("format") != FORMAT:
        raise ValueError(f"Unsupported import format; expected {FORMAT!r}")
    if document.get("version") != FORMAT_VERSION:
        raise ValueError(
            f"Unsupported {FORMAT} version: {document.get('version')!r}; expected {FORMAT_VERSION}"
        )
    memories = document.get("memories")
    if not isinstance(memories, list):
        raise ValueError("memories must be an array")
    parsed = [_parse_memory(raw, index, scope_mode) for index, raw in enumerate(memories)]

    keys: set[tuple[str, str]] = set()
    for item in parsed:
        key = (item["name"].casefold(), item["scope_cwd"])
        if key in keys:
            raise ValueError(
                f"Import contains duplicate memory {item['name']!r} in scope {item['scope_cwd'] or 'global'}"
            )
        keys.add(key)
    return parsed


def _export(argv: Sequence[str]) -> int:
    args = _export_parser().parse_args(argv)
    with TmemDB(db_path()) as db:
        if args.names:
            memories: list[Memory] = []
            for name in args.names:
                memory = db.resolve_memory(name, current_scope_cwd())
                if memory is None:
                    print(f"Unknown memory: {name}", file=sys.stderr)
                    return 1
                memories.append(memory)
        else:
            memories = db.list_memories()
        document = {
            "format": FORMAT,
            "version": FORMAT_VERSION,
            "memories": [_memory_payload(db, memory) for memory in memories],
        }

    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        destination = Path(args.output).expanduser()
        if destination.exists() and not args.force:
            print(f"Refusing to overwrite existing file: {destination}; use --force", file=sys.stderr)
            return 1
        destination.write_text(text, encoding="utf-8")
        print(f"Exported {len(memories)} memories to {destination}")
    else:
        sys.stdout.write(text)
    return 0


def _replace_memory(db: TmemDB, existing: Memory, definition: dict[str, Any]) -> None:
    # Import replaces only the reusable definition. Local usage counters and
    # remembered parameter values remain local activity and are preserved.
    db.update_memory(
        existing.id,
        name=definition["name"],
        description=definition["description"],
        stop_on_error=definition["stop_on_error"],
        steps=definition["commands"],
        defaults=definition["defaults"],
        scope_cwd=definition["scope_cwd"],
    )
    with db.connection:
        db.connection.execute(
            "UPDATE memories SET shell = ? WHERE id = ?",
            (definition["shell"], existing.id),
        )


def _import(argv: Sequence[str]) -> int:
    args = _import_parser().parse_args(argv)
    document = _read_document(args.path)
    definitions = _validated_import(document, args.scope)

    imported = 0
    skipped = 0
    replaced = 0
    with TmemDB(db_path()) as db:
        conflicts: list[tuple[dict[str, Any], Memory]] = []
        for definition in definitions:
            existing = db.get_memory_in_scope(definition["name"], definition["scope_cwd"])
            if existing is not None:
                conflicts.append((definition, existing))
        if conflicts and args.on_conflict == "error":
            names = ", ".join(
                f"{definition['name']}@{definition['scope_cwd'] or 'global'}"
                for definition, _ in conflicts
            )
            raise ValueError(f"Import conflicts with existing memories: {names}")

        for definition in definitions:
            existing = db.get_memory_in_scope(definition["name"], definition["scope_cwd"])
            if existing is not None:
                if args.on_conflict == "skip":
                    skipped += 1
                    continue
                _replace_memory(db, existing, definition)
                imported += 1
                replaced += 1
                continue
            db.create_memory(
                definition["name"],
                definition["commands"],
                description=definition["description"],
                stop_on_error=definition["stop_on_error"],
                defaults=definition["defaults"],
                scope_cwd=definition["scope_cwd"],
                shell=definition["shell"],
            )
            imported += 1

    parts = [f"Imported {imported} memories"]
    if replaced:
        parts.append(f"replaced {replaced}")
    if skipped:
        parts.append(f"skipped {skipped}")
    print("; ".join(parts) + ".")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        # Transfer verbs return before cli.main: configure their pipes too.
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure") and not stream.isatty():
                stream.reconfigure(encoding="utf-8")
        # Shell adapters probe unknown first words as potential memory names.
        # Keep transfer verbs reserved so a memory called "export" or "import"
        # cannot shadow these management commands.
        if len(arguments) == 2 and arguments[0] == "memory-exists" and arguments[1] in TRANSFER_COMMANDS:
            return 1
        if arguments[:1] == ["export"]:
            return _export(arguments[1:])
        if arguments[:1] == ["import"]:
            return _import(arguments[1:])
        return cli_main(arguments)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
