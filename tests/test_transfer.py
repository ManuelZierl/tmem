from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tmem.db import TmemDB, normalize_scope_cwd
from tmem.transfer_cli import main


class MemoryTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.source_db = self.root / "source.db"
        self.target_db = self.root / "target.db"
        self.environment = patch.dict(
            os.environ,
            {
                "TMEM_DB": str(self.source_db),
                "TMEM_CONFIG_DIR": self.tempdir.name,
            },
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.tempdir.cleanup()

    def test_round_trip_preserves_definition_shell_scope_and_defaults(self) -> None:
        project = normalize_scope_cwd(str(self.root / "project"))
        with TmemDB(self.source_db) as db:
            db.create_memory(
                "deploy",
                ["Write-Output {{target}}", "Write-Output done"],
                description="Deploy target",
                stop_on_error=False,
                defaults={"target": "staging"},
                scope_cwd=project,
                shell="powershell",
            )
            db.create_memory("status", ["git status"], shell="bash")

        exported = io.StringIO()
        with contextlib.redirect_stdout(exported):
            self.assertEqual(main(["export"]), 0)
        document = json.loads(exported.getvalue())
        self.assertEqual(document["format"], "tmem-memories")
        self.assertEqual(document["version"], 1)
        self.assertEqual(len(document["memories"]), 2)

        with patch.dict(os.environ, {"TMEM_DB": str(self.target_db)}):
            with patch("sys.stdin", io.StringIO(exported.getvalue())):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["import", "-"]), 0)
            with TmemDB(self.target_db) as db:
                deploy = db.get_memory_in_scope("deploy", project)
                self.assertIsNotNone(deploy)
                self.assertEqual(deploy.shell, "powershell")
                self.assertEqual(deploy.description, "Deploy target")
                self.assertFalse(deploy.stop_on_error)
                self.assertEqual(
                    [step.command_template for step in deploy.steps],
                    ["Write-Output {{target}}", "Write-Output done"],
                )
                defaults = {item.name: item.default_value for item in db.parameter_definitions(deploy.id)}
                self.assertEqual(defaults, {"target": "staging"})
                self.assertEqual(db.get_memory("status").shell, "bash")

    def test_scope_can_be_remapped_to_global_or_here(self) -> None:
        document = {
            "format": "tmem-memories",
            "version": 1,
            "memories": [
                {
                    "name": "local",
                    "description": "",
                    "shell": "zsh",
                    "scope": {"kind": "directory", "path": "/old/machine/project"},
                    "stop_on_error": True,
                    "commands": ["pwd"],
                    "parameter_defaults": {},
                }
            ],
        }
        data = json.dumps(document)
        with patch.dict(os.environ, {"TMEM_DB": str(self.target_db)}):
            with patch("sys.stdin", io.StringIO(data)), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["import", "-", "--scope", "global"]), 0)
            with TmemDB(self.target_db) as db:
                self.assertIsNotNone(db.get_memory("local"))

        second_db = self.root / "here.db"
        target_scope = normalize_scope_cwd(str(self.root / "new-project"))
        with patch.dict(os.environ, {"TMEM_DB": str(second_db)}):
            with patch("tmem.transfer_cli.current_scope_cwd", return_value=target_scope):
                with patch("sys.stdin", io.StringIO(data)), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["import", "-", "--scope", "here"]), 0)
            with TmemDB(second_db) as db:
                self.assertIsNotNone(db.get_memory_in_scope("local", target_scope))

    def test_conflicts_fail_closed_and_support_skip_and_replace(self) -> None:
        with TmemDB(self.source_db) as db:
            db.create_memory("same", ["echo new"], shell="bash")
        exported = io.StringIO()
        with contextlib.redirect_stdout(exported):
            self.assertEqual(main(["export", "same"]), 0)

        with patch.dict(os.environ, {"TMEM_DB": str(self.target_db)}):
            with TmemDB(self.target_db) as db:
                db.create_memory("same", ["echo old"], shell="zsh")

            error = io.StringIO()
            with patch("sys.stdin", io.StringIO(exported.getvalue())), contextlib.redirect_stderr(error):
                self.assertEqual(main(["import", "-"]), 1)
            self.assertIn("conflicts with existing memories", error.getvalue())

            with patch("sys.stdin", io.StringIO(exported.getvalue())), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["import", "-", "--on-conflict", "skip"]), 0)
            with TmemDB(self.target_db) as db:
                self.assertEqual(db.get_memory("same").shell, "zsh")

            with patch("sys.stdin", io.StringIO(exported.getvalue())), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["import", "-", "--on-conflict", "replace"]), 0)
            with TmemDB(self.target_db) as db:
                memory = db.get_memory("same")
                self.assertEqual(memory.shell, "bash")
                self.assertEqual(memory.steps[0].command_template, "echo new")

    def test_rejects_unknown_format_version_before_writing(self) -> None:
        invalid = json.dumps({"format": "tmem-memories", "version": 99, "memories": []})
        error = io.StringIO()
        with patch.dict(os.environ, {"TMEM_DB": str(self.target_db)}):
            with patch("sys.stdin", io.StringIO(invalid)), contextlib.redirect_stderr(error):
                self.assertEqual(main(["import", "-"]), 1)
            with TmemDB(self.target_db) as db:
                self.assertEqual(db.list_memories(), [])
        self.assertIn("Unsupported tmem-memories version", error.getvalue())

    def test_transfer_verbs_are_reserved_from_memory_lookup(self) -> None:
        with TmemDB(self.source_db) as db:
            db.create_memory("export", ["echo shadow"], shell="bash")
        self.assertEqual(main(["memory-exists", "export"]), 1)


if __name__ == "__main__":
    unittest.main()
