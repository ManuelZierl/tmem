from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Optional

from .models import HistoryEntry, Memory, MemoryStep, ParameterDefinition
from .templates import parameter_names
from .shells import active_shell, SHELLS


def now_ms() -> int:
    return int(time.time() * 1000)


def validate_memory_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ValueError(
            "Memory names may contain letters, digits, dots, underscores, and hyphens"
        )


def validate_memory_steps(steps: list[str]) -> None:
    if not steps or any(not isinstance(step, str) or not step.strip() for step in steps):
        raise ValueError("A memory needs one or more non-empty commands")


def _validate_limit(limit: int) -> None:
    if limit < 0:
        raise ValueError("limit must not be negative")


def normalize_scope_cwd(value: Optional[str]) -> str:
    if not value:
        return ""
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Memory directories must be absolute paths")
    return os.path.normcase(str(path.resolve(strict=False)))


def current_scope_cwd() -> str:
    return normalize_scope_cwd(str(Path.cwd()))


class TmemDB:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=5.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self._migrate()
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "TmemDB":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _migrate(self) -> None:
        if self.connection.execute("PRAGMA user_version").fetchone()[0] > 3:
            raise ValueError("This database was created by a newer tmem version")
        self._migrate_memory_scopes()
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command TEXT NOT NULL,
                    cwd TEXT NOT NULL DEFAULT '',
                    exit_code INTEGER,
                    started_at_ms INTEGER,
                    finished_at_ms INTEGER NOT NULL,
                    duration_ms INTEGER,
                    hostname TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT '',
                    shell TEXT NOT NULL DEFAULT 'bash',
                    source TEXT NOT NULL DEFAULT 'live',
                    source_key TEXT UNIQUE
                );

                CREATE INDEX IF NOT EXISTS history_finished_idx
                    ON history(finished_at_ms DESC);
                CREATE INDEX IF NOT EXISTS history_cwd_idx
                    ON history(cwd, finished_at_ms DESC);
                CREATE INDEX IF NOT EXISTS history_exit_idx
                    ON history(exit_code, finished_at_ms DESC);

                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL COLLATE NOCASE,
                    scope_cwd TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    stop_on_error INTEGER NOT NULL DEFAULT 1,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    last_run_at_ms INTEGER,
                    run_count INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(name, scope_cwd)
                );

                CREATE INDEX IF NOT EXISTS memories_scope_idx
                    ON memories(scope_cwd, name COLLATE NOCASE);

                CREATE TABLE IF NOT EXISTS memory_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    command_template TEXT NOT NULL,
                    UNIQUE(memory_id, position)
                );

                CREATE TABLE IF NOT EXISTS memory_parameters (
                    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    default_value TEXT,
                    position INTEGER NOT NULL,
                    PRIMARY KEY(memory_id, name)
                );

                CREATE TABLE IF NOT EXISTS parameter_values (
                    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    value TEXT NOT NULL,
                    use_count INTEGER NOT NULL DEFAULT 1,
                    last_used_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(memory_id, name, value),
                    FOREIGN KEY(memory_id, name)
                        REFERENCES memory_parameters(memory_id, name)
                        ON DELETE CASCADE
                );

                """
            )
        with self.connection:
            # Serialized check/add also permits concurrent shell startups.
            self.connection.execute("BEGIN IMMEDIATE")
            columns = self.connection.execute("PRAGMA table_info(memories)").fetchall()
            if not any(row["name"] == "shell" for row in columns):
                self.connection.execute("ALTER TABLE memories ADD COLUMN shell TEXT NOT NULL DEFAULT 'bash'")
            self.connection.execute("PRAGMA user_version = 3")
        violations = self.connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("Database migration left invalid memory references")

    def _migrate_memory_scopes(self) -> None:
        columns = self.connection.execute("PRAGMA table_info(memories)").fetchall()
        if not columns or any(row["name"] == "scope_cwd" for row in columns):
            return

        self.connection.commit()
        self.connection.execute("PRAGMA foreign_keys = OFF")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            columns = self.connection.execute("PRAGMA table_info(memories)").fetchall()
            if any(row["name"] == "scope_cwd" for row in columns):
                self.connection.commit()
                return

            statements = [
                """
                CREATE TABLE memories_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL COLLATE NOCASE,
                    scope_cwd TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    stop_on_error INTEGER NOT NULL DEFAULT 1,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    last_run_at_ms INTEGER,
                    run_count INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(name, scope_cwd)
                )
                """,
                """
                CREATE TABLE memory_steps_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER NOT NULL REFERENCES memories_v2(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    command_template TEXT NOT NULL,
                    UNIQUE(memory_id, position)
                )
                """,
                """
                CREATE TABLE memory_parameters_v2 (
                    memory_id INTEGER NOT NULL REFERENCES memories_v2(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    default_value TEXT,
                    position INTEGER NOT NULL,
                    PRIMARY KEY(memory_id, name)
                )
                """,
                """
                CREATE TABLE parameter_values_v2 (
                    memory_id INTEGER NOT NULL REFERENCES memories_v2(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    value TEXT NOT NULL,
                    use_count INTEGER NOT NULL DEFAULT 1,
                    last_used_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(memory_id, name, value),
                    FOREIGN KEY(memory_id, name)
                        REFERENCES memory_parameters_v2(memory_id, name)
                        ON DELETE CASCADE
                )
                """,
                """
                INSERT INTO memories_v2(
                    id, name, scope_cwd, description, stop_on_error, created_at_ms,
                    updated_at_ms, last_run_at_ms, run_count
                )
                SELECT id, name, '', description, stop_on_error, created_at_ms,
                       updated_at_ms, last_run_at_ms, run_count
                FROM memories
                """,
                "INSERT INTO memory_steps_v2 SELECT * FROM memory_steps",
                "INSERT INTO memory_parameters_v2 SELECT * FROM memory_parameters",
                "INSERT INTO parameter_values_v2 SELECT * FROM parameter_values",
                "DROP TABLE parameter_values",
                "DROP TABLE memory_parameters",
                "DROP TABLE memory_steps",
                "DROP TABLE memories",
                "ALTER TABLE memories_v2 RENAME TO memories",
                "ALTER TABLE memory_steps_v2 RENAME TO memory_steps",
                "ALTER TABLE memory_parameters_v2 RENAME TO memory_parameters",
                "ALTER TABLE parameter_values_v2 RENAME TO parameter_values",
                "PRAGMA user_version = 2",
            ]
            for statement in statements:
                self.connection.execute(statement)
            violations = self.connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError("Database migration left invalid memory references")
            self.connection.commit()
        except BaseException:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise
        finally:
            self.connection.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _history_from_row(row: sqlite3.Row) -> HistoryEntry:
        return HistoryEntry(
            id=row["id"],
            command=row["command"],
            cwd=row["cwd"],
            exit_code=row["exit_code"],
            started_at_ms=row["started_at_ms"],
            finished_at_ms=row["finished_at_ms"],
            duration_ms=row["duration_ms"],
            hostname=row["hostname"],
            session_id=row["session_id"],
            shell=row["shell"],
        )

    def record_history(
        self,
        command: str,
        cwd: str,
        exit_code: Optional[int],
        started_at_ms: Optional[int],
        finished_at_ms: Optional[int],
        hostname: str,
        session_id: str,
        shell: str = "bash",
        source: str = "live",
        source_key: Optional[str] = None,
    ) -> Optional[int]:
        command = command.rstrip("\n")
        if not command.strip():
            return None
        finished = now_ms() if finished_at_ms is None else finished_at_ms
        duration = None
        if started_at_ms is not None:
            duration = max(0, finished - started_at_ms)
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO history(
                    command, cwd, exit_code, started_at_ms, finished_at_ms,
                    duration_ms, hostname, session_id, shell, source, source_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO NOTHING
                """,
                (
                    command,
                    cwd,
                    exit_code,
                    started_at_ms,
                    finished,
                    duration,
                    hostname,
                    session_id,
                    shell,
                    source,
                    source_key,
                ),
            )
        return int(cursor.lastrowid) if cursor.rowcount else None

    def list_history(
        self,
        limit: int = 5000,
        cwd: Optional[str] = None,
        failed_only: bool = False,
        since_ms: Optional[int] = None,
    ) -> list[HistoryEntry]:
        _validate_limit(limit)
        clauses: list[str] = []
        params: list[object] = []
        if cwd is not None:
            clauses.append("cwd = ?")
            params.append(cwd)
        if failed_only:
            clauses.append("exit_code IS NOT NULL AND exit_code != 0")
        if since_ms is not None:
            clauses.append("finished_at_ms >= ?")
            params.append(since_ms)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = self.connection.execute(
            f"""
            SELECT * FROM history
            {where}
            ORDER BY finished_at_ms DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [self._history_from_row(row) for row in rows]

    def history_count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS count FROM history").fetchone()
        return int(row["count"])

    def history_status_counts(self) -> tuple[int, int, int]:
        row = self.connection.execute(
            """
            SELECT
                SUM(CASE WHEN exit_code = 0 THEN 1 ELSE 0 END) AS successful,
                SUM(CASE WHEN exit_code IS NOT NULL AND exit_code != 0 THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN exit_code IS NULL THEN 1 ELSE 0 END) AS unknown
            FROM history
            """
        ).fetchone()
        return (
            int(row["successful"] or 0),
            int(row["failed"] or 0),
            int(row["unknown"] or 0),
        )

    def top_commands(self, limit: int = 15) -> list[tuple[str, int]]:
        _validate_limit(limit)
        rows = self.connection.execute(
            """
            SELECT command, COUNT(*) AS count
            FROM history
            GROUP BY command
            ORDER BY count DESC, MAX(finished_at_ms) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [(row["command"], int(row["count"])) for row in rows]

    def get_history(self, ids: Iterable[int]) -> list[HistoryEntry]:
        ids_list = list(dict.fromkeys(ids))
        if not ids_list:
            return []
        placeholders = ",".join("?" for _ in ids_list)
        rows = self.connection.execute(
            f"SELECT * FROM history WHERE id IN ({placeholders})", ids_list
        ).fetchall()
        by_id = {row["id"]: self._history_from_row(row) for row in rows}
        return [by_id[item_id] for item_id in ids_list if item_id in by_id]

    def delete_history(self, ids: Iterable[int]) -> int:
        ids_list = list(dict.fromkeys(ids))
        if not ids_list:
            return 0
        placeholders = ",".join("?" for _ in ids_list)
        with self.connection:
            cursor = self.connection.execute(
                f"DELETE FROM history WHERE id IN ({placeholders})", ids_list
            )
        return cursor.rowcount

    def occurrences(self, command: str, limit: int = 100) -> list[HistoryEntry]:
        _validate_limit(limit)
        rows = self.connection.execute(
            """
            SELECT * FROM history
            WHERE command = ?
            ORDER BY finished_at_ms DESC, id DESC
            LIMIT ?
            """,
            (command, limit),
        ).fetchall()
        return [self._history_from_row(row) for row in rows]

    def create_memory(
        self,
        name: str,
        steps: list[str],
        description: str = "",
        stop_on_error: bool = True,
        defaults: Optional[dict[str, str]] = None,
        scope_cwd: Optional[str] = None,
        shell: Optional[str] = None,
    ) -> Memory:
        shell = shell or active_shell()
        if shell not in SHELLS:
            raise ValueError(f"Unsupported shell: {shell}")
        validate_memory_name(name)
        validate_memory_steps(steps)
        normalized_scope = normalize_scope_cwd(scope_cwd)
        timestamp = now_ms()
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO memories(
                    name, scope_cwd, description, stop_on_error, created_at_ms, updated_at_ms, shell
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (name, normalized_scope, description, int(stop_on_error), timestamp, timestamp, shell),
            )
            memory_id = int(cursor.lastrowid)
            self.connection.executemany(
                """
                INSERT INTO memory_steps(memory_id, position, command_template)
                VALUES (?, ?, ?)
                """,
                [(memory_id, index, step) for index, step in enumerate(steps)],
            )
            self._sync_parameters(memory_id, steps, defaults or {})
        memory = self.get_memory(memory_id)
        if memory is None:
            raise RuntimeError("Failed to load newly-created memory")
        return memory

    def update_memory(
        self,
        memory_id: int,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        stop_on_error: Optional[bool] = None,
        steps: Optional[list[str]] = None,
        defaults: Optional[dict[str, str]] = None,
        scope_cwd: Optional[str] = None,
    ) -> Memory:
        current = self.get_memory(memory_id)
        if current is None:
            raise KeyError(f"Unknown memory id: {memory_id}")
        next_name = name if name is not None else current.name
        validate_memory_name(next_name)
        next_description = description if description is not None else current.description
        next_stop = stop_on_error if stop_on_error is not None else current.stop_on_error
        next_scope = current.scope_cwd if scope_cwd is None else normalize_scope_cwd(scope_cwd)
        next_steps = steps if steps is not None else [step.command_template for step in current.steps]
        validate_memory_steps(next_steps)
        with self.connection:
            self.connection.execute(
                """
                UPDATE memories
                SET name = ?, scope_cwd = ?, description = ?, stop_on_error = ?, updated_at_ms = ?
                WHERE id = ?
                """,
                (next_name, next_scope, next_description, int(next_stop), now_ms(), memory_id),
            )
            if steps is not None:
                self.connection.execute("DELETE FROM memory_steps WHERE memory_id = ?", (memory_id,))
                self.connection.executemany(
                    """
                    INSERT INTO memory_steps(memory_id, position, command_template)
                    VALUES (?, ?, ?)
                    """,
                    [(memory_id, index, step) for index, step in enumerate(next_steps)],
                )
            self._sync_parameters(memory_id, next_steps, defaults or {})
        updated = self.get_memory(memory_id)
        if updated is None:
            raise RuntimeError("Failed to load updated memory")
        return updated

    def _sync_parameters(
        self,
        memory_id: int,
        templates: list[str],
        defaults: dict[str, str],
    ) -> None:
        names = parameter_names(templates)
        existing_rows = self.connection.execute(
            "SELECT name, default_value FROM memory_parameters WHERE memory_id = ?",
            (memory_id,),
        ).fetchall()
        existing = {row["name"]: row["default_value"] for row in existing_rows}

        # Upsert definitions so remembered values survive ordinary edits. Only
        # parameters removed from every command template are deleted.
        for index, name in enumerate(names):
            default_value = defaults.get(name, existing.get(name))
            self.connection.execute(
                """
                INSERT INTO memory_parameters(memory_id, name, default_value, position)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(memory_id, name) DO UPDATE SET
                    default_value = excluded.default_value,
                    position = excluded.position
                """,
                (memory_id, name, default_value, index),
            )
        obsolete = set(existing) - set(names)
        if obsolete:
            placeholders = ",".join("?" for _ in obsolete)
            self.connection.execute(
                f"DELETE FROM memory_parameters WHERE memory_id = ? AND name IN ({placeholders})",
                [memory_id, *sorted(obsolete)],
            )

    def list_memories(self) -> list[Memory]:
        rows = self.connection.execute(
            """
            SELECT * FROM memories
            ORDER BY COALESCE(last_run_at_ms, updated_at_ms) DESC, name COLLATE NOCASE
            """
        ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def list_available_memories(self, scope_cwd: str) -> list[Memory]:
        normalized_scope = normalize_scope_cwd(scope_cwd)
        rows = self.connection.execute(
            """
            SELECT * FROM memories AS memory
            WHERE memory.scope_cwd = ?
               OR (
                    memory.scope_cwd = ''
                    AND NOT EXISTS (
                        SELECT 1 FROM memories AS local
                        WHERE local.scope_cwd = ? AND local.name = memory.name
                    )
               )
            ORDER BY COALESCE(last_run_at_ms, updated_at_ms) DESC, name COLLATE NOCASE
            """,
            (normalized_scope, normalized_scope),
        ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def _memory_from_row(self, row: sqlite3.Row) -> Memory:
        step_rows = self.connection.execute(
            """
            SELECT * FROM memory_steps
            WHERE memory_id = ?
            ORDER BY position
            """,
            (row["id"],),
        ).fetchall()
        steps = [
            MemoryStep(
                id=step["id"],
                memory_id=step["memory_id"],
                position=step["position"],
                command_template=step["command_template"],
            )
            for step in step_rows
        ]
        return Memory(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            stop_on_error=bool(row["stop_on_error"]),
            created_at_ms=row["created_at_ms"],
            updated_at_ms=row["updated_at_ms"],
            last_run_at_ms=row["last_run_at_ms"],
            run_count=row["run_count"],
            scope_cwd=row["scope_cwd"],
            shell=row["shell"],
            steps=steps,
        )

    def get_memory(self, identifier: int | str) -> Optional[Memory]:
        if isinstance(identifier, int):
            row = self.connection.execute(
                "SELECT * FROM memories WHERE id = ?", (identifier,)
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT * FROM memories WHERE name = ? COLLATE NOCASE AND scope_cwd = ''",
                (identifier,),
            ).fetchone()
        return self._memory_from_row(row) if row else None

    def get_memory_in_scope(self, name: str, scope_cwd: str) -> Optional[Memory]:
        normalized_scope = normalize_scope_cwd(scope_cwd)
        row = self.connection.execute(
            "SELECT * FROM memories WHERE name = ? COLLATE NOCASE AND scope_cwd = ?",
            (name, normalized_scope),
        ).fetchone()
        return self._memory_from_row(row) if row else None

    def resolve_memory(self, name: str, scope_cwd: str) -> Optional[Memory]:
        normalized_scope = normalize_scope_cwd(scope_cwd)
        row = self.connection.execute(
            """
            SELECT * FROM memories
            WHERE name = ? COLLATE NOCASE AND scope_cwd IN (?, '')
            ORDER BY CASE WHEN scope_cwd = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (name, normalized_scope, normalized_scope),
        ).fetchone()
        return self._memory_from_row(row) if row else None

    def delete_memory(self, identifier: int | str) -> bool:
        memory = self.get_memory(identifier)
        if memory is None:
            return False
        with self.connection:
            self.connection.execute("DELETE FROM memories WHERE id = ?", (memory.id,))
        return True

    def parameter_definitions(self, memory_id: int) -> list[ParameterDefinition]:
        rows = self.connection.execute(
            """
            SELECT * FROM memory_parameters
            WHERE memory_id = ?
            ORDER BY position
            """,
            (memory_id,),
        ).fetchall()
        return [
            ParameterDefinition(
                memory_id=row["memory_id"],
                name=row["name"],
                default_value=row["default_value"],
                position=row["position"],
            )
            for row in rows
        ]

    def parameter_values(self, memory_id: int, name: str, limit: int = 20) -> list[str]:
        _validate_limit(limit)
        rows = self.connection.execute(
            """
            SELECT value FROM parameter_values
            WHERE memory_id = ? AND name = ?
            ORDER BY last_used_at_ms DESC, use_count DESC
            LIMIT ?
            """,
            (memory_id, name, limit),
        ).fetchall()
        return [row["value"] for row in rows]

    def remember_parameter_value(self, memory_id: int, name: str, value: str) -> None:
        timestamp = now_ms()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO parameter_values(memory_id, name, value, use_count, last_used_at_ms)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(memory_id, name, value) DO UPDATE SET
                    use_count = parameter_values.use_count + 1,
                    last_used_at_ms = excluded.last_used_at_ms
                """,
                (memory_id, name, value, timestamp),
            )

    def clear_parameter_values(self, memory_id: int, name: str) -> int:
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM parameter_values WHERE memory_id = ? AND name = ?",
                (memory_id, name),
            )
        return cursor.rowcount

    def set_parameter_default(self, memory_id: int, name: str, value: Optional[str]) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE memory_parameters SET default_value = ?
                WHERE memory_id = ? AND name = ?
                """,
                (value, memory_id, name),
            )

    def mark_memory_run(self, memory_id: int) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE memories
                SET last_run_at_ms = ?, run_count = run_count + 1
                WHERE id = ?
                """,
                (now_ms(), memory_id),
            )

    def import_bash_history(self, path: Path) -> tuple[int, int]:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
        imported = 0
        skipped = 0
        pending_timestamp_ms: Optional[int] = None
        ordinal = 0
        for line in raw:
            if line.startswith("#") and line[1:].isdigit() and len(line[1:]) >= 9:
                pending_timestamp_ms = int(line[1:]) * 1000
                continue
            if not line.strip():
                continue
            ordinal += 1
            digest = hashlib.sha256(
                f"{path.resolve()}\0{ordinal}\0{pending_timestamp_ms}\0{line}".encode("utf-8")
            ).hexdigest()
            result = self.record_history(
                command=line,
                cwd="",
                exit_code=None,
                started_at_ms=pending_timestamp_ms,
                finished_at_ms=now_ms() if pending_timestamp_ms is None else pending_timestamp_ms,
                hostname="",
                session_id="import",
                source="bash-history",
                source_key=digest,
            )
            if result is None:
                skipped += 1
            else:
                imported += 1
            pending_timestamp_ms = None
        return imported, skipped


    def import_history(self, path: Path, shell: str) -> tuple[int, int]:
        if shell == "bash":
            return self.import_bash_history(path)
        if shell not in {"zsh", "powershell"}:
            raise ValueError(f"Unsupported history format: {shell}")
        # Zsh EXTENDED_HISTORY and PSReadLine use escaped physical newlines.
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        records = []
        pending = ""
        timestamp = None
        escape = "`" if shell == "powershell" else "\\"
        for line in lines:
            if not pending and shell == "zsh":
                match = re.match(r"^: (\d+):(\d+);(.*)$", line)
                if match:
                    timestamp = int(match.group(1)) * 1000
                    line = match.group(3)
            count = len(line) - len(line.rstrip(escape))
            if count % 2:
                pending += line[:-1] + "\n"
                continue
            command = pending + line
            pending = ""
            if command.strip():
                records.append((command, timestamp))
            timestamp = None
        if pending.strip():
            records.append((pending.rstrip("\n"), timestamp))
        imported = skipped = 0
        for ordinal, (command, stamp) in enumerate(records, 1):
            digest = hashlib.sha256(
                f"{shell}\0{path.resolve()}\0{ordinal}\0{stamp}\0{command}".encode("utf-8")
            ).hexdigest()
            result = self.record_history(
                command=command, cwd="", exit_code=None, started_at_ms=stamp,
                finished_at_ms=stamp if stamp is not None else now_ms(),
                hostname="", session_id="import", shell=shell,
                source=f"{shell}-history", source_key=digest,
            )
            if result is None:
                skipped += 1
            else:
                imported += 1
        return imported, skipped
