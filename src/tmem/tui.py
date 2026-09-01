from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from .db import (
    TmemDB,
    current_scope_cwd,
    normalize_scope_cwd,
    validate_memory_name,
)
from .models import HistoryEntry, Memory
from .templates import (
    apply_parameterization,
    build_script,
    render_template,
    shell_tokens,
)
from .terminal_ui import (
    confirm,
    decode_hidden,
    encode_hidden,
    prompt_text,
    run_fzf,
    run_on_terminal,
    show_text,
)


@dataclass(slots=True)
class Execution:
    script: str
    display: str
    memory_id: Optional[int] = None


@dataclass(slots=True)
class ItemRef:
    kind: str
    id: int

    @classmethod
    def parse(cls, row: str) -> "ItemRef":
        hidden = row.split("\t", 1)[0]
        kind, raw_id = hidden.split(":", 1)
        return cls(kind=kind, id=int(raw_id))


class TmemUI:
    def __init__(self, db: TmemDB, history_limit: int = 5000):
        self.db = db
        self.history_limit = history_limit
        self.scope_cwd = current_scope_cwd()

    def run(self) -> Optional[Execution]:
        while True:
            rows = self._main_rows()
            if not rows:
                show_text(
                    "tmem",
                    [
                        "No history has been recorded yet.",
                        "Open a new shell after installation, run a few commands, and try again.",
                    ],
                )
                return None
            result = run_fzf(
                rows,
                header=(
                    "tmem — type to fuzzy-search (for example: dcker → docker)\n"
                    "Enter run  ·  → details/actions  ·  Tab mark multiple  ·  Esc close"
                ),
                prompt="history> ",
                multi=True,
                expect=("enter", "right"),
            )
            if result is None:
                return None
            refs = [ItemRef.parse(row) for row in result.rows]
            if result.key == "enter":
                execution = self._execution_for_refs(refs)
                if execution is not None:
                    return execution
                continue
            action_result = self._open_actions(refs)
            if isinstance(action_result, Execution):
                return action_result

    def _main_rows(self) -> list[str]:
        rows: list[str] = []
        for memory in self.db.list_available_memories(self.scope_cwd):
            icon = "▦" if memory.is_group else "★"
            count = f" [{len(memory.steps)} steps]" if memory.is_group else ""
            scope = "global" if memory.is_global else "here"
            summary = self._memory_summary(memory)
            rows.append(f"m:{memory.id}\t{icon} {memory.name} [{scope}]{count}  {summary}")

        for entry in self.db.list_history(limit=self.history_limit):
            time_text = self._format_time(entry.finished_at_ms)
            status = "?" if entry.exit_code is None else ("✓" if entry.exit_code == 0 else f"×{entry.exit_code}")
            cwd = self._short_cwd(entry.cwd)
            command = self._one_line(entry.command)
            rows.append(f"h:{entry.id}\t{time_text:<11} {status:<4} {cwd:<24} {command}")
        return rows

    @staticmethod
    def _memory_summary(memory: Memory) -> str:
        if not memory.steps:
            return ""
        first = TmemUI._one_line(memory.steps[0].command_template)
        if len(memory.steps) == 1:
            return first
        return first + "  →  " + TmemUI._one_line(memory.steps[1].command_template)

    @staticmethod
    def _one_line(value: str, max_length: int = 140) -> str:
        compact = " ↵ ".join(part.strip() for part in value.splitlines() if part.strip())
        compact = compact.replace("\t", " ")
        return compact if len(compact) <= max_length else compact[: max_length - 1] + "…"

    @staticmethod
    def _short_cwd(cwd: str) -> str:
        if not cwd:
            return "—"
        home = str(Path.home())
        if cwd == home:
            return "~"
        if cwd.startswith(home + os.sep):
            cwd = "~" + cwd[len(home) :]
        if len(cwd) <= 24:
            return cwd
        parts = Path(cwd).parts
        return "…/" + "/".join(parts[-2:])

    @staticmethod
    def _format_time(timestamp_ms: int) -> str:
        value = datetime.fromtimestamp(timestamp_ms / 1000)
        now = datetime.now()
        if value.date() == now.date():
            return value.strftime("%H:%M:%S")
        if value.year == now.year:
            return value.strftime("%b %d %H:%M")
        return value.strftime("%Y-%m-%d")

    def _execution_for_refs(self, refs: Sequence[ItemRef]) -> Optional[Execution]:
        if not refs:
            return None
        if len(refs) == 1:
            ref = refs[0]
            if ref.kind == "h":
                entries = self.db.get_history([ref.id])
                if not entries:
                    return None
                return Execution(script=entries[0].command, display=entries[0].command)
            memory = self.db.get_memory(ref.id)
            return self.resolve_memory(memory) if memory else None

        if any(ref.kind != "h" for ref in refs):
            show_text(
                "Cannot combine these selections",
                [
                    "Multi-selection currently accepts history commands only.",
                    "Select one saved memory at a time, or mark history rows to create/run a group.",
                ],
            )
            return None
        entries = self.db.get_history([ref.id for ref in refs])
        entries.sort(key=lambda item: (item.finished_at_ms, item.id))
        commands = [entry.command for entry in entries]
        return Execution(
            script=build_script(commands, stop_on_error=True),
            display=" &&\n".join(commands),
        )

    def _open_actions(self, refs: Sequence[ItemRef]) -> Optional[Execution]:
        if not refs:
            return None
        if len(refs) > 1:
            return self._multi_actions(refs)
        ref = refs[0]
        if ref.kind == "h":
            entries = self.db.get_history([ref.id])
            return self._history_actions(entries[0]) if entries else None
        memory = self.db.get_memory(ref.id)
        return self._memory_actions(memory) if memory else None

    def _choose_action(self, title: str, details: list[str], actions: list[tuple[str, str]]) -> Optional[str]:
        rows = [f"{code}\t{label}" for code, label in actions]
        result = run_fzf(
            rows,
            header=title + "\n" + "\n".join(details) + "\n\nEnter choose  ·  ← back",
            prompt="action> ",
            multi=False,
            expect=("enter", "left"),
            no_sort=True,
        )
        if result is None or result.key == "left":
            return None
        return result.rows[0].split("\t", 1)[0]

    def _history_actions(self, entry: HistoryEntry) -> Optional[Execution]:
        while True:
            details = [
                self._one_line(entry.command, 220),
                f"Directory: {entry.cwd or 'unknown'}",
                f"Exit code: {entry.exit_code if entry.exit_code is not None else 'unknown'}",
                f"Executed: {datetime.fromtimestamp(entry.finished_at_ms / 1000).isoformat(sep=' ', timespec='seconds')}",
            ]
            action = self._choose_action(
                "History command",
                details,
                [
                    ("run", "Run"),
                    ("edit-run", "Edit before running"),
                    ("save", "Save as global memory"),
                    ("save-here", "Save as memory for this directory"),
                    ("parameterize", "Save as global parameterized memory"),
                    ("parameterize-here", "Save as parameterized memory for this directory"),
                    ("group", "Create a global group with this command…"),
                    ("group-here", "Create a directory group with this command…"),
                    ("occurrences", "Show all occurrences"),
                    ("copy", "Copy command"),
                    ("delete", "Delete from tmem history"),
                ],
            )
            if action is None:
                return None
            if action == "run":
                return Execution(entry.command, entry.command)
            if action == "edit-run":
                edited = self._edit_command_before_run(entry.command)
                if edited:
                    return Execution(edited, edited)
            elif action == "save":
                self._save_memory([entry.command], defaults={})
                return None
            elif action == "save-here":
                self._save_memory([entry.command], defaults={}, scope_cwd=self.scope_cwd)
                return None
            elif action in {"parameterize", "parameterize-here"}:
                parameterized = self._parameterize_commands([entry.command])
                if parameterized:
                    commands, defaults = parameterized
                    scope = self.scope_cwd if action == "parameterize-here" else ""
                    self._save_memory(commands, defaults, scope_cwd=scope)
                return None
            elif action in {"group", "group-here"}:
                scope = self.scope_cwd if action == "group-here" else ""
                self._group_builder(initial_id=entry.id, scope_cwd=scope)
                return None
            elif action == "occurrences":
                matches = self.db.occurrences(entry.command)
                show_text(
                    "Occurrences",
                    [
                        f"{self._format_time(item.finished_at_ms):<12} "
                        f"exit={item.exit_code!s:<4} {item.cwd or '—'}"
                        for item in matches
                    ],
                )
            elif action == "copy":
                self._copy(entry.command)
                return None
            elif action == "delete":
                if confirm("Delete history command", "Delete this occurrence from tmem?"):
                    self.db.delete_history([entry.id])
                return None

    def _multi_actions(self, refs: Sequence[ItemRef]) -> Optional[Execution]:
        history_refs = [ref for ref in refs if ref.kind == "h"]
        if len(history_refs) != len(refs):
            show_text(
                "Multiple selection",
                ["Groups can currently be built from history commands only."],
            )
            return None
        entries = self.db.get_history([ref.id for ref in history_refs])
        entries.sort(key=lambda item: (item.finished_at_ms, item.id))
        commands = [entry.command for entry in entries]
        action = self._choose_action(
            f"{len(commands)} selected commands",
            [f"{index + 1}. {self._one_line(command)}" for index, command in enumerate(commands)],
            [
                ("run", "Run in chronological order"),
                ("save", "Save as global command group"),
                ("save-here", "Save as command group for this directory"),
                ("parameterize", "Save as global parameterized command group"),
                ("parameterize-here", "Save as parameterized group for this directory"),
                ("delete", "Delete selected history occurrences"),
            ],
        )
        if action == "run":
            return Execution(build_script(commands, True), " &&\n".join(commands))
        if action == "save":
            self._save_memory(commands, defaults={})
        elif action == "save-here":
            self._save_memory(commands, defaults={}, scope_cwd=self.scope_cwd)
        elif action in {"parameterize", "parameterize-here"}:
            parameterized = self._parameterize_commands(commands)
            if parameterized:
                templates, defaults = parameterized
                scope = self.scope_cwd if action == "parameterize-here" else ""
                self._save_memory(templates, defaults, scope_cwd=scope)
        elif action == "delete":
            if confirm("Delete selected history", f"Delete {len(entries)} occurrences from tmem?"):
                self.db.delete_history([entry.id for entry in entries])
        return None

    def _group_builder(self, initial_id: Optional[int] = None, scope_cwd: str = "") -> None:
        entries = self.db.list_history(limit=self.history_limit)
        rows = [
            f"h:{entry.id}\t{self._format_time(entry.finished_at_ms):<11} "
            f"{self._short_cwd(entry.cwd):<24} {self._one_line(entry.command)}"
            for entry in entries
        ]
        query = None
        result = run_fzf(
            rows,
            header=(
                "Select commands for the group. They will be ordered by original execution time.\n"
                "Tab mark/unmark  ·  Enter continue  ·  Esc cancel"
            ),
            prompt="group> ",
            multi=True,
            expect=("enter",),
            query=query,
        )
        if result is None:
            return
        ids = [ItemRef.parse(row).id for row in result.rows]
        if initial_id is not None and initial_id not in ids:
            ids.append(initial_id)
        selected = self.db.get_history(ids)
        selected.sort(key=lambda item: (item.finished_at_ms, item.id))
        if not selected:
            return
        self._save_memory(
            [item.command for item in selected], defaults={}, scope_cwd=scope_cwd
        )

    def _save_memory(
        self, commands: list[str], defaults: dict[str, str], scope_cwd: str = ""
    ) -> Optional[Memory]:
        details = [f"{index + 1}. {self._one_line(command, 180)}" for index, command in enumerate(commands)]
        while True:
            name = prompt_text(
                "Save command memory",
                "Name",
                details=details,
                allow_empty=False,
            )
            if name is None:
                return None
            name = name.strip()
            try:
                validate_memory_name(name)
            except ValueError:
                show_text(
                    "Invalid memory name",
                    ["Use letters, digits, dots, underscores, or hyphens."],
                )
                continue
            if self.db.get_memory_in_scope(name, scope_cwd) is not None:
                scope = scope_cwd or "global"
                show_text(
                    "Name already exists",
                    [f"A memory named {name!r} already exists in {scope}."],
                )
                continue
            break
        description = prompt_text(
            "Save command memory",
            "Optional description",
            details=details,
            allow_empty=True,
        )
        try:
            return self.db.create_memory(
                name=name,
                steps=commands,
                description=description or "",
                stop_on_error=True,
                defaults=defaults,
                scope_cwd=scope_cwd,
            )
        except sqlite3.IntegrityError:
            show_text("Could not save memory", [f"A memory named {name!r} already exists."])
            return None

    def _parameterize_commands(
        self, commands: list[str]
    ) -> Optional[tuple[list[str], dict[str, str]]]:
        token_rows: list[str] = []
        token_lookup: dict[str, tuple[int, int, int, str]] = {}
        for command_index, command in enumerate(commands):
            for token_index, token in enumerate(shell_tokens(command)):
                key = f"{command_index}:{token_index}"
                token_lookup[key] = (command_index, token.start, token.end, token.value)
                token_rows.append(
                    f"{key}\tCommand {command_index + 1}: {token.raw}"
                )
        result = run_fzf(
            token_rows,
            header=(
                "Choose whole shell tokens to turn into parameters.\n"
                "Tab mark/unmark  ·  Enter continue  ·  Esc cancel"
            ),
            prompt="parameterize> ",
            multi=True,
            expect=("enter",),
            no_sort=True,
        )
        if result is None:
            return None

        chosen: list[tuple[int, int, int, str]] = []
        for row in result.rows:
            key = row.split("\t", 1)[0]
            if key in token_lookup:
                chosen.append(token_lookup[key])
        chosen.sort(key=lambda item: (item[0], item[1]))
        if not chosen:
            return None

        replacements: dict[int, list[tuple[int, int, str]]] = {}
        defaults: dict[str, str] = {}
        used_names: dict[str, str] = {}
        for command_index, start, end, value in chosen:
            while True:
                name = prompt_text(
                    "Name parameter",
                    "Parameter name",
                    details=[
                        f"Command {command_index + 1}: {commands[command_index]}",
                        f"Selected value: {value}",
                    ],
                    allow_empty=False,
                )
                if name is None:
                    return None
                name = name.strip().replace("-", "_")
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                    show_text(
                        "Invalid parameter name",
                        ["Use letters, digits, and underscores; begin with a letter or underscore."],
                    )
                    continue
                if name in used_names and used_names[name] != value:
                    show_text(
                        "Conflicting default values",
                        [
                            f"Parameter {name!r} already uses default {used_names[name]!r}.",
                            "Choose a different name or select occurrences with the same value.",
                        ],
                    )
                    continue
                break
            replacements.setdefault(command_index, []).append((start, end, name))
            used_names[name] = value
            defaults[name] = value

        templates = list(commands)
        for command_index, command_replacements in replacements.items():
            templates[command_index] = apply_parameterization(
                templates[command_index], command_replacements
            )
        return templates, defaults

    def resolve_memory(
        self,
        memory: Memory,
        overrides: Optional[dict[str, str]] = None,
        use_defaults_without_prompt: bool = False,
    ) -> Optional[Execution]:
        overrides = overrides or {}
        definitions = self.db.parameter_definitions(memory.id)
        known = {definition.name for definition in definitions}
        unknown = set(overrides) - known
        if unknown:
            raise ValueError(
                f"Unknown parameters for {memory.name}: {', '.join(sorted(unknown))}"
            )

        values: dict[str, str] = {}
        for definition in definitions:
            if definition.name in overrides:
                value = overrides[definition.name]
            elif use_defaults_without_prompt and definition.default_value is not None:
                value = definition.default_value
            else:
                recent = self.db.parameter_values(memory.id, definition.name)
                value = self._prompt_parameter(
                    memory.name,
                    definition.name,
                    definition.default_value,
                    recent,
                )
                if value is None:
                    return None
            values[definition.name] = value
            self.db.remember_parameter_value(memory.id, definition.name, value)

        try:
            commands = [render_template(step.command_template, values) for step in memory.steps]
        except KeyError as error:
            show_text("Cannot run memory", [str(error)])
            return None
        if len(commands) == 1:
            display = commands[0]
        else:
            separator = " &&\n" if memory.stop_on_error else "\n"
            display = separator.join(commands)
        return Execution(
            script=build_script(commands, memory.stop_on_error),
            display=display,
            memory_id=memory.id,
        )

    def _prompt_parameter(
        self,
        memory_name: str,
        name: str,
        default: Optional[str],
        recent: list[str],
    ) -> Optional[str]:
        candidates: list[tuple[str, str]] = []
        seen: set[str] = set()
        if default is not None:
            candidates.append(("default", default))
            seen.add(default)
        for value in recent:
            if value not in seen:
                candidates.append(("value", value))
                seen.add(value)
        if candidates:
            rows = [
                f"v:{encode_hidden(value)}\t{'Default' if kind == 'default' else 'Recent'}: {value}"
                for kind, value in candidates
            ]
            rows.append("new:\tEnter a new value…")
            result = run_fzf(
                rows,
                header=(
                    f"{memory_name} — parameter: {name}\n"
                    "Enter choose or use unmatched text  ·  Esc cancel"
                ),
                prompt=f"{name}> ",
                multi=False,
                expect=(),
                no_sort=True,
                accept_query=True,
            )
            if result is None:
                return None
            if not result.rows:
                return result.query
            hidden = result.rows[0].split("\t", 1)[0]
            if hidden.startswith("v:"):
                return decode_hidden(hidden[2:])
        return prompt_text(
            f"{memory_name} — parameter",
            name,
            default=default,
            allow_empty=default is not None,
        )

    def _memory_actions(self, memory: Memory) -> Optional[Execution]:
        while True:
            details = [
                memory.description or "(no description)",
                f"Directory: {memory.scope_cwd or 'global'}",
                f"Runs: {memory.run_count}",
                f"Mode: {'stop on first failure' if memory.stop_on_error else 'always continue'}",
            ] + [
                f"{index + 1}. {self._one_line(step.command_template, 200)}"
                for index, step in enumerate(memory.steps)
            ]
            actions = [
                ("run", "Run"),
                ("edit-run", "Edit resolved command before running"),
                ("edit", "Edit name, directory, description, and commands in $EDITOR"),
                ("rename", "Rename"),
                (
                    "scope",
                    "Bind to current directory" if memory.is_global else "Make global",
                ),
                ("params", "Manage parameter defaults and remembered values"),
            ]
            if memory.is_group:
                actions.append(("append", "Add commands from history"))
                actions.append(("toggle-stop", "Toggle stop-on-failure mode"))
            actions.extend(
                [
                    ("copy", "Copy command template"),
                    ("delete", "Delete memory"),
                ]
            )
            action = self._choose_action(memory.name, details, actions)
            if action is None:
                return None
            if action == "run":
                return self.resolve_memory(memory)
            if action == "edit-run":
                execution = self.resolve_memory(memory)
                if execution is None:
                    continue
                edited = self._edit_command_before_run(execution.display)
                if edited:
                    return Execution(edited, edited, memory.id)
            elif action == "edit":
                updated = self._edit_memory(memory)
                if updated is not None:
                    memory = updated
            elif action == "rename":
                new_name = prompt_text(
                    "Rename memory",
                    "Name",
                    default=memory.name,
                    allow_empty=False,
                )
                new_name = new_name.strip() if new_name else ""
                if new_name and new_name != memory.name:
                    try:
                        memory = self.db.update_memory(memory.id, name=new_name)
                    except (ValueError, sqlite3.IntegrityError) as error:
                        show_text("Cannot rename", [str(error)])
            elif action == "params":
                self._manage_parameters(memory)
            elif action == "scope":
                target_scope = self.scope_cwd if memory.is_global else ""
                try:
                    memory = self.db.update_memory(memory.id, scope_cwd=target_scope)
                except sqlite3.IntegrityError:
                    target = target_scope or "global"
                    show_text(
                        "Cannot change directory",
                        [f"A memory named {memory.name!r} already exists in {target}."],
                    )
            elif action == "append":
                self._append_history(memory)
                refreshed = self.db.get_memory(memory.id)
                if refreshed:
                    memory = refreshed
            elif action == "toggle-stop":
                memory = self.db.update_memory(memory.id, stop_on_error=not memory.stop_on_error)
            elif action == "copy":
                self._copy("\n".join(step.command_template for step in memory.steps))
                return None
            elif action == "delete":
                scope = memory.scope_cwd or "global"
                if confirm("Delete memory", f"Delete {memory.name!r} from {scope}?"):
                    self.db.delete_memory(memory.id)
                    return None

    def _append_history(self, memory: Memory) -> None:
        entries = self.db.list_history(limit=self.history_limit)
        rows = [
            f"h:{entry.id}\t{self._format_time(entry.finished_at_ms):<11} "
            f"{self._short_cwd(entry.cwd):<24} {self._one_line(entry.command)}"
            for entry in entries
        ]
        result = run_fzf(
            rows,
            header="Select commands to append\nTab mark/unmark  ·  Enter append  ·  Esc cancel",
            prompt="append> ",
            multi=True,
            expect=("enter",),
        )
        if result is None:
            return
        ids = [ItemRef.parse(row).id for row in result.rows]
        selected = self.db.get_history(ids)
        selected.sort(key=lambda item: (item.finished_at_ms, item.id))
        if selected:
            steps = [step.command_template for step in memory.steps] + [item.command for item in selected]
            self.db.update_memory(memory.id, steps=steps)

    def _manage_parameters(self, memory: Memory) -> None:
        while True:
            definitions = self.db.parameter_definitions(memory.id)
            if not definitions:
                show_text(
                    "Parameters",
                    ["This memory has no {{parameter}} placeholders."],
                )
                return
            rows = [
                f"p:{definition.name}\t{definition.name}  default={definition.default_value!r}"
                for definition in definitions
            ]
            result = run_fzf(
                rows,
                header=f"{memory.name} parameters\nEnter edit default  ·  ← back",
                prompt="parameter> ",
                multi=False,
                expect=("enter", "left"),
                no_sort=True,
            )
            if result is None or result.key == "left":
                return
            name = result.rows[0].split("\t", 1)[0][2:]
            definition = next(item for item in definitions if item.name == name)
            recent = self.db.parameter_values(memory.id, name)
            action = self._choose_action(
                f"Parameter: {name}",
                [
                    f"Current default: {definition.default_value!r}",
                    f"Remembered values: {len(recent)}",
                ],
                [
                    ("default", "Set or clear default"),
                    ("values", "Show remembered values"),
                    ("clear", "Forget remembered values"),
                ],
            )
            if action == "default":
                value = prompt_text(
                    "Parameter default",
                    name,
                    details=[
                        f"Current default: {definition.default_value!r}",
                        "Leave empty to remove the default.",
                    ],
                    allow_empty=True,
                )
                if value is not None:
                    self.db.set_parameter_default(memory.id, name, value or None)
            elif action == "values":
                show_text("Remembered values", recent or ["(none)"])
            elif action == "clear":
                if confirm("Forget values", f"Forget all remembered values for {name!r}?"):
                    self.db.clear_parameter_values(memory.id, name)

    def _edit_command_before_run(self, command: str) -> Optional[str]:
        if "\n" not in command:
            return prompt_text(
                "Edit before running",
                "Command",
                default=command,
                details=["The edited command will run in the current shell."],
                allow_empty=False,
            )

        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
        with tempfile.NamedTemporaryFile(
            mode="w+", suffix=".sh", encoding="utf-8", delete=False
        ) as handle:
            handle.write(command)
            handle.write("\n")
            path = Path(handle.name)
        try:
            try:
                result = run_on_terminal([*shlex.split(editor), str(path)])
            except (OSError, ValueError) as error:
                show_text("Editor failed", [str(error)])
                return None
            if result.returncode != 0:
                return None
            edited = path.read_text(encoding="utf-8").rstrip("\n")
            return edited or None
        finally:
            try:
                path.unlink()
            except OSError:
                pass

    def _edit_memory(self, memory: Memory) -> Optional[Memory]:
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
        payload = {
            "name": memory.name,
            "directory": memory.scope_cwd or None,
            "description": memory.description,
            "stop_on_error": memory.stop_on_error,
            "commands": [step.command_template for step in memory.steps],
        }
        with tempfile.NamedTemporaryFile(
            mode="w+", suffix=".json", encoding="utf-8", delete=False
        ) as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            path = Path(handle.name)
        try:
            result = run_on_terminal([*shlex.split(editor), str(path)])
            if result.returncode != 0:
                show_text("Editor failed", [f"{editor} exited with status {result.returncode}."])
                return None
            updated = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(updated, dict):
                raise ValueError("The edited memory must be a JSON object")
            unexpected = set(updated) - {
                "name",
                "directory",
                "description",
                "stop_on_error",
                "commands",
            }
            if unexpected:
                raise ValueError(f"Unknown fields: {', '.join(sorted(unexpected))}")

            name = updated.get("name", memory.name)
            directory = updated.get("directory", memory.scope_cwd or None)
            description = updated.get("description", "")
            stop_on_error = updated.get("stop_on_error", True)
            commands = updated.get("commands")
            if not isinstance(name, str):
                raise ValueError("name must be a string")
            if directory is not None and not isinstance(directory, str):
                raise ValueError("directory must be an absolute path or null")
            if not isinstance(description, str):
                raise ValueError("description must be a string")
            if not isinstance(stop_on_error, bool):
                raise ValueError("stop_on_error must be true or false")
            if not isinstance(commands, list) or not commands or not all(
                isinstance(item, str) and item.strip() for item in commands
            ):
                raise ValueError("commands must be a non-empty list of strings")
            return self.db.update_memory(
                memory.id,
                name=name,
                scope_cwd=normalize_scope_cwd(directory),
                description=description,
                stop_on_error=stop_on_error,
                steps=commands,
            )
        except (OSError, json.JSONDecodeError, ValueError, sqlite3.IntegrityError) as error:
            show_text("Could not update memory", [str(error)])
            return None
        finally:
            try:
                path.unlink()
            except OSError:
                pass

    def _copy(self, value: str) -> None:
        commands: list[list[str]] = []
        if shutil.which("wl-copy"):
            commands.append(["wl-copy"])
        if shutil.which("xclip"):
            commands.append(["xclip", "-selection", "clipboard"])
        if shutil.which("xsel"):
            commands.append(["xsel", "--clipboard", "--input"])
        for command in commands:
            result = subprocess.run(command, input=value, text=True, check=False)
            if result.returncode == 0:
                show_text("Copied", [self._one_line(value, 220)])
                return
        show_text(
            "Clipboard utility not found",
            [
                value,
                "",
                "Install wl-clipboard, xclip, or xsel to copy directly.",
            ],
        )
