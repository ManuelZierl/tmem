from __future__ import annotations

import json
import tempfile
import unittest
import os
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from tmem.db import TmemDB, normalize_scope_cwd
from tmem.terminal_ui import FzfResult
from tmem.tui import ItemRef, TmemUI


class TuiLogicTests(unittest.TestCase):
    def test_marked_history_commands_run_oldest_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TmemDB(Path(directory) / "tmem.db") as db:
                tag_id = db.record_history(
                    "git tag v1.0.0", "/repo", 0, 90, 100, "h", "s"
                )
                push_id = db.record_history(
                    "git push origin v1.0.0", "/repo", 0, 190, 200, "h", "s"
                )
                assert tag_id is not None and push_id is not None
                execution = TmemUI(db)._execution_for_refs(
                    [ItemRef("h", push_id), ItemRef("h", tag_id)]
                )
                self.assertIsNotNone(execution)
                assert execution is not None
                self.assertEqual(
                    execution.display,
                    "git tag v1.0.0 &&\ngit push origin v1.0.0",
                )
                self.assertLess(
                    execution.script.index("git tag v1.0.0"),
                    execution.script.index("git push origin v1.0.0"),
                )

    def test_continue_mode_display_matches_executed_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TmemDB(Path(directory) / "tmem.db") as db:
                memory = db.create_memory(
                    "continue", ["false", "echo still-runs"], stop_on_error=False
                )
                execution = TmemUI(db).resolve_memory(memory)
                self.assertIsNotNone(execution)
                self.assertEqual(execution.script, "false\necho still-runs")
                self.assertEqual(execution.display, execution.script)

    def test_unmatched_parameter_query_is_used_and_remembered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TmemDB(Path(directory) / "tmem.db") as db:
                memory = db.create_memory(
                    "tmux-start",
                    ["tmux a -t {{session}}"],
                    defaults={"session": "qaiva"},
                )
                result = FzfResult(key="enter", rows=[], query="llmops")
                with patch("tmem.tui.run_fzf", return_value=result):
                    execution = TmemUI(db).resolve_memory(memory)
                self.assertIsNotNone(execution)
                assert execution is not None
                self.assertEqual(execution.script, "tmux a -t llmops")
                self.assertEqual(db.parameter_values(memory.id, "session"), ["llmops"])

    def test_memory_actions_use_fresh_state_after_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TmemDB(Path(directory) / "tmem.db") as db:
                memory = db.create_memory("editable", ["echo old"])
                ui = TmemUI(db)

                def edit(current):
                    return db.update_memory(current.id, steps=["echo new"])

                with patch.object(ui, "_choose_action", side_effect=["edit", "run"]):
                    with patch.object(ui, "_edit_memory", side_effect=edit):
                        execution = ui._memory_actions(memory)
                self.assertEqual(execution.display, "echo new")

    def test_invalid_rename_stays_in_action_view(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TmemDB(Path(directory) / "tmem.db") as db:
                memory = db.create_memory("valid", ["echo ok"])
                ui = TmemUI(db)
                with patch.object(ui, "_choose_action", side_effect=["rename", None]):
                    with patch("tmem.tui.prompt_text", return_value="bad name"):
                        with patch("tmem.tui.show_text") as show:
                            self.assertIsNone(ui._memory_actions(memory))
                show.assert_called_once()
                self.assertIsNotNone(db.get_memory("valid"))

    def test_editor_rejects_non_object_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TmemDB(Path(directory) / "tmem.db") as db:
                memory = db.create_memory("editable", ["echo old"])

                def write_invalid(command):
                    Path(command[-1]).write_text("[]\n", encoding="utf-8")
                    return SimpleNamespace(returncode=0)

                with patch("tmem.tui.run_on_terminal", side_effect=write_invalid):
                    with patch("tmem.tui.show_text") as show:
                        self.assertIsNone(TmemUI(db)._edit_memory(memory))
                self.assertIn("JSON object", show.call_args.args[1][0])
                self.assertEqual(db.get_memory(memory.id).steps[0].command_template, "echo old")

    def test_main_rows_show_only_effective_memory_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TmemDB(Path(directory) / "tmem.db") as db:
                db.create_memory("watch", ["echo global"])
                db.create_memory("watch", ["echo local"], scope_cwd=normalize_scope_cwd(str(Path("/project").resolve())))
                db.create_memory("other", ["echo elsewhere"], scope_cwd=normalize_scope_cwd(str(Path("/other").resolve())))
                with patch("tmem.tui.current_scope_cwd", return_value=normalize_scope_cwd(str(Path("/project").resolve()))):
                    rows = TmemUI(db)._main_rows()
                memory_rows = [row for row in rows if row.startswith("m:")]
                self.assertEqual(len(memory_rows), 1)
                self.assertIn("watch [here]", memory_rows[0])
                self.assertIn("echo local", memory_rows[0])

    def test_memory_scope_action_binds_global_memory_here(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TmemDB(Path(directory) / "tmem.db") as db:
                memory = db.create_memory("watch", ["echo global"])
                with patch("tmem.tui.current_scope_cwd", return_value=normalize_scope_cwd(str(Path("/project").resolve()))):
                    ui = TmemUI(db)
                with patch.object(ui, "_choose_action", side_effect=["scope", None]):
                    self.assertIsNone(ui._memory_actions(memory))
                self.assertEqual(db.get_memory(memory.id).scope_cwd, normalize_scope_cwd(str(Path("/project").resolve())))

    def test_editor_can_change_memory_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TmemDB(Path(directory) / "tmem.db") as db:
                memory = db.create_memory("watch", ["echo global"])

                def change_directory(command):
                    path = Path(command[-1])
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    payload["directory"] = normalize_scope_cwd(str(Path("/project").resolve()))
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    return SimpleNamespace(returncode=0)

                with patch("tmem.tui.run_on_terminal", side_effect=change_directory):
                    updated = TmemUI(db)._edit_memory(memory)
                self.assertEqual(updated.scope_cwd, normalize_scope_cwd(str(Path("/project").resolve())))


if __name__ == "__main__":
    unittest.main()
