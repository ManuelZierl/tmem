from __future__ import annotations

import tempfile
import unittest
import stat
import sqlite3
from pathlib import Path

from tmem.db import TmemDB


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = TmemDB(Path(self.tempdir.name) / "tmem.db")

    def tearDown(self) -> None:
        self.db.close()
        self.tempdir.cleanup()

    def test_history_keeps_duplicate_executions(self) -> None:
        for finished in (1000, 2000):
            self.db.record_history(
                command="docker ps",
                cwd="/project",
                exit_code=0,
                started_at_ms=finished - 10,
                finished_at_ms=finished,
                hostname="host",
                session_id="session",
            )
        entries = self.db.list_history()
        self.assertEqual([item.command for item in entries], ["docker ps", "docker ps"])
        self.assertEqual(entries[0].duration_ms, 10)
        self.assertEqual(self.db.history_count(), 2)
        self.assertEqual(self.db.history_status_counts(), (2, 0, 0))
        self.assertEqual(self.db.top_commands(1), [("docker ps", 2)])

    def test_group_and_parameter_values_survive_edit(self) -> None:
        memory = self.db.create_memory(
            "release",
            ["git tag {{tag}}", "git push origin {{tag}}"],
            defaults={"tag": "v1.0.0"},
        )
        self.db.remember_parameter_value(memory.id, "tag", "v1.1.0")
        self.db.update_memory(memory.id, description="Release and push")
        definitions = self.db.parameter_definitions(memory.id)
        self.assertEqual(definitions[0].default_value, "v1.0.0")
        self.assertEqual(self.db.parameter_values(memory.id, "tag"), ["v1.1.0"])

    def test_removed_parameter_removes_its_values(self) -> None:
        memory = self.db.create_memory("echo", ["echo {{value}}"], defaults={"value": "x"})
        self.db.remember_parameter_value(memory.id, "value", "y")
        self.db.update_memory(memory.id, steps=["echo fixed"])
        self.assertEqual(self.db.parameter_definitions(memory.id), [])
        self.assertEqual(self.db.parameter_values(memory.id, "value"), [])

    def test_history_import_is_idempotent(self) -> None:
        path = Path(self.tempdir.name) / "history"
        path.write_text("#1700000000\necho one\necho two\n", encoding="utf-8")
        self.assertEqual(self.db.import_bash_history(path), (2, 0))
        self.assertEqual(self.db.import_bash_history(path), (0, 2))

    def test_epoch_zero_timestamp_is_preserved(self) -> None:
        self.db.record_history("epoch", "", 0, 0, 0, "", "")
        entry = self.db.list_history()[0]
        self.assertEqual(entry.finished_at_ms, 0)
        self.assertEqual(entry.duration_ms, 0)

    def test_memory_steps_must_all_be_non_empty(self) -> None:
        with self.assertRaises(ValueError):
            self.db.create_memory("invalid", ["echo ok", ""])
        memory = self.db.create_memory("valid", ["echo ok"])
        with self.assertRaises(ValueError):
            self.db.update_memory(memory.id, steps=["   "])
        self.assertEqual(self.db.get_memory(memory.id).steps[0].command_template, "echo ok")

    def test_existing_database_parent_permissions_are_unchanged(self) -> None:
        parent = Path(self.tempdir.name) / "shared"
        parent.mkdir(mode=0o755)
        parent.chmod(0o755)
        with TmemDB(parent / "other.db"):
            pass
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)

    def test_negative_limits_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.db.list_history(limit=-1)
        with self.assertRaises(ValueError):
            self.db.top_commands(limit=-1)

    def test_directory_memory_shadows_global_memory(self) -> None:
        global_memory = self.db.create_memory("watch", ["echo global"])
        local_a = self.db.create_memory(
            "watch", ["echo a"], scope_cwd="/projects/a"
        )
        local_b = self.db.create_memory(
            "watch", ["echo b"], scope_cwd="/projects/b"
        )

        self.assertEqual(self.db.resolve_memory("watch", "/projects/a").id, local_a.id)
        self.assertEqual(self.db.resolve_memory("watch", "/projects/b").id, local_b.id)
        self.assertEqual(self.db.resolve_memory("watch", "/projects/other").id, global_memory.id)
        self.assertEqual(self.db.get_memory("watch").id, global_memory.id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.create_memory("WATCH", ["echo duplicate"], scope_cwd="/projects/a")

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.update_memory(local_a.id, scope_cwd="")
        self.assertEqual(self.db.get_memory(local_a.id).scope_cwd, "/projects/a")

    def test_available_memories_hide_shadowed_global_name(self) -> None:
        global_watch = self.db.create_memory("watch", ["echo global"])
        global_logs = self.db.create_memory("logs", ["echo logs"])
        local_watch = self.db.create_memory("watch", ["echo local"], scope_cwd="/project")
        available = self.db.list_available_memories("/project")
        self.assertEqual({memory.id for memory in available}, {global_logs.id, local_watch.id})
        self.assertNotIn(global_watch.id, {memory.id for memory in available})

    def test_old_database_migrates_memories_to_global_scope(self) -> None:
        path = Path(self.tempdir.name) / "old.db"
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                description TEXT NOT NULL DEFAULT '',
                stop_on_error INTEGER NOT NULL DEFAULT 1,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                last_run_at_ms INTEGER,
                run_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE memory_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                command_template TEXT NOT NULL,
                UNIQUE(memory_id, position)
            );
            CREATE TABLE memory_parameters (
                memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                default_value TEXT,
                position INTEGER NOT NULL,
                PRIMARY KEY(memory_id, name)
            );
            CREATE TABLE parameter_values (
                memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                value TEXT NOT NULL,
                use_count INTEGER NOT NULL DEFAULT 1,
                last_used_at_ms INTEGER NOT NULL,
                PRIMARY KEY(memory_id, name, value),
                FOREIGN KEY(memory_id, name)
                    REFERENCES memory_parameters(memory_id, name) ON DELETE CASCADE
            );
            INSERT INTO memories VALUES (7, 'release', 'Release', 1, 10, 20, 30, 4);
            INSERT INTO memory_steps VALUES (9, 7, 0, 'echo {{tag}}');
            INSERT INTO memory_parameters VALUES (7, 'tag', 'v1', 0);
            INSERT INTO parameter_values VALUES (7, 'tag', 'v2', 3, 40);
            """
        )
        connection.close()

        with TmemDB(path) as migrated:
            memory = migrated.get_memory("release")
            self.assertEqual(memory.id, 7)
            self.assertTrue(memory.is_global)
            self.assertEqual(memory.run_count, 4)
            self.assertEqual(memory.last_run_at_ms, 30)
            self.assertEqual(memory.steps[0].id, 9)
            self.assertEqual(migrated.parameter_definitions(7)[0].default_value, "v1")
            self.assertEqual(migrated.parameter_values(7, "tag"), ["v2"])
            migrated.create_memory("release", ["echo local"], scope_cwd="/project")
            self.assertEqual(migrated.connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(migrated.connection.execute("PRAGMA foreign_key_check").fetchall(), [])

        with TmemDB(path) as reopened:
            self.assertEqual(len(reopened.list_memories()), 2)


if __name__ == "__main__":
    unittest.main()
