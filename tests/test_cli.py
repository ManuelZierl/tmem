from __future__ import annotations

import base64
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tmem.cli import main
from tmem.db import TmemDB


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "tmem.db"
        self.environment = patch.dict(
            os.environ,
            {
                "TMEM_DB": str(self.db_path),
                "TMEM_CONFIG_DIR": self.tempdir.name,
            },
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.tempdir.cleanup()

    def test_noninteractive_fuzzy_search(self) -> None:
        with TmemDB(self.db_path) as db:
            db.record_history("docker compose logs", "/project", 0, 1, 2, "h", "s")
            db.record_history("git status", "/project", 0, 3, 4, "h", "s")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(["search", "dcker"])
        self.assertEqual(status, 0)
        self.assertIn("docker compose logs", output.getvalue())
        self.assertNotIn("git status", output.getvalue())

    def test_save_and_show_memory(self) -> None:
        self.assertEqual(main(["save", "logs", "--", "docker", "compose", "logs"]), 0)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["show", "logs"]), 0)
        self.assertIn("docker compose logs", output.getvalue())

    def test_list_includes_parameterized_memory(self) -> None:
        self.assertEqual(main(["save", "logs", "--", "echo", "{{target}}"]), 0)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["list"]), 0)
        self.assertIn("logs", output.getvalue())
        self.assertIn("params=target", output.getvalue())

    def test_description_option_is_stored(self) -> None:
        self.assertEqual(
            main(["save", "--description", "Useful logs", "logs", "--", "echo", "ok"]),
            0,
        )
        with TmemDB(self.db_path) as db:
            self.assertEqual(db.get_memory("logs").description, "Useful logs")

    def test_unknown_parameter_is_an_error(self) -> None:
        self.assertEqual(main(["save", "echo", "--", "echo", "{{value}}"]), 0)
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            status = main(["shell-run", "echo", "other=x"])
        self.assertEqual(status, 2)
        self.assertIn("Unknown parameters", error.getvalue())

    def test_positional_parameter_value_uses_template_order(self) -> None:
        self.assertEqual(main(["save", "catfile", "--", "cat", "{{file}}"]), 0)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["shell-run", "catfile", "README file.md"]), 0)
        script = base64.b64decode(output.getvalue().split("\t")[1]).decode()
        self.assertEqual(script, "cat 'README file.md'")

    def test_positional_and_named_parameters_can_be_mixed(self) -> None:
        self.assertEqual(
            main(["save", "pair", "--", "echo", "{{first}}", "{{second}}"]), 0
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["shell-run", "pair", "second=b", "a"]), 0)
        script = base64.b64decode(output.getvalue().split("\t")[1]).decode()
        self.assertEqual(script, "echo a b")

    def test_directory_scoped_memory_resolution(self) -> None:
        with patch("tmem.cli.current_scope_cwd", return_value="/projects/a") as cwd:
            self.assertEqual(main(["save", "watch", "--", "echo", "global"]), 0)
            self.assertEqual(
                main(["save", "--here", "watch", "--", "echo", "local-a"]), 0
            )
            cwd.return_value = "/projects/b"
            self.assertEqual(
                main(["save", "--here", "watch", "--", "echo", "local-b"]), 0
            )

            cwd.return_value = "/projects/a"
            local_output = io.StringIO()
            with contextlib.redirect_stdout(local_output):
                self.assertEqual(main(["shell-run", "watch"]), 0)
            local_script = base64.b64decode(local_output.getvalue().split("\t")[1]).decode()
            self.assertEqual(local_script, "echo local-a")

            global_output = io.StringIO()
            with contextlib.redirect_stdout(global_output):
                self.assertEqual(main(["shell-run", "--global", "watch"]), 0)
            global_script = base64.b64decode(global_output.getvalue().split("\t")[1]).decode()
            self.assertEqual(global_script, "echo global")

            cwd.return_value = "/projects/other"
            fallback_output = io.StringIO()
            with contextlib.redirect_stdout(fallback_output):
                self.assertEqual(main(["shell-run", "watch"]), 0)
            fallback_script = base64.b64decode(fallback_output.getvalue().split("\t")[1]).decode()
            self.assertEqual(fallback_script, "echo global")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["list"]), 0)
        self.assertEqual(output.getvalue().count("watch"), 3)
        self.assertIn("scope=global", output.getvalue())
        self.assertIn("scope=/projects/a", output.getvalue())

    def test_directory_scoped_group(self) -> None:
        with patch("tmem.cli.current_scope_cwd", return_value="/project"):
            self.assertEqual(
                main(["group", "--here", "release", "--", "echo one", ":::", "echo two"]),
                0,
            )
        with TmemDB(self.db_path) as db:
            memory = db.get_memory_in_scope("release", "/project")
        self.assertIsNotNone(memory)
        self.assertTrue(memory.is_group)

    def test_negative_limit_is_rejected(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(["search", "--limit", "-1"])
        self.assertEqual(raised.exception.code, 2)

    def test_invalid_config_reports_a_clear_error(self) -> None:
        Path(self.tempdir.name, "config.json").write_text("[]\n", encoding="utf-8")
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            status = main(["doctor"])
        self.assertEqual(status, 1)
        self.assertIn("must contain a JSON object", error.getvalue())

    def test_record_preserves_epoch_zero(self) -> None:
        with patch("sys.stdin", io.StringIO("epoch")):
            self.assertEqual(
                main(
                    [
                        "record",
                        "--cwd",
                        "/",
                        "--started-at-ms",
                        "0",
                        "--finished-at-ms",
                        "0",
                    ]
                ),
                0,
            )
        with TmemDB(self.db_path) as db:
            entry = db.list_history()[0]
        self.assertEqual(entry.finished_at_ms, 0)
        self.assertEqual(entry.duration_ms, 0)

    def test_shell_ui_emits_execution_protocol_from_fzf_selection(self) -> None:
        with TmemDB(self.db_path) as db:
            db.record_history("echo from-ui", "/project", 0, 1, 2, "h", "s")
        bin_dir = Path(self.tempdir.name) / "bin"
        bin_dir.mkdir()
        fzf = bin_dir / "fzf"
        fzf.write_text(
            "#!/usr/bin/env bash\n"
            "IFS= read -r first\n"
            "printf 'enter\\n%s\\n' \"$first\"\n",
            encoding="utf-8",
        )
        fzf.chmod(0o755)
        output = io.StringIO()
        with patch.dict(os.environ, {"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}):
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["shell-ui"]), 0)
        action, script_encoded, display_encoded, _ = output.getvalue().rstrip("\n").split("\t")
        self.assertEqual(action, "execute")
        self.assertEqual(base64.b64decode(script_encoded).decode(), "echo from-ui")
        self.assertEqual(base64.b64decode(display_encoded).decode(), "echo from-ui")

    def test_right_arrow_opens_actions_and_run_is_selectable(self) -> None:
        with TmemDB(self.db_path) as db:
            db.record_history("echo via-details", "/project", 0, 1, 2, "h", "s")
        bin_dir = Path(self.tempdir.name) / "right-bin"
        bin_dir.mkdir()
        counter = Path(self.tempdir.name) / "fzf-counter"
        fzf = bin_dir / "fzf"
        fzf.write_text(
            "#!/usr/bin/env bash\n"
            "count=0\n"
            "[[ -f \"$TMEM_FAKE_FZF_COUNTER\" ]] && count=$(cat \"$TMEM_FAKE_FZF_COUNTER\")\n"
            "IFS= read -r first\n"
            "if [[ $count == 0 ]]; then key=right; else key=enter; fi\n"
            "printf '%s' $((count + 1)) > \"$TMEM_FAKE_FZF_COUNTER\"\n"
            "printf '%s\\n%s\\n' \"$key\" \"$first\"\n",
            encoding="utf-8",
        )
        fzf.chmod(0o755)
        output = io.StringIO()
        with patch.dict(
            os.environ,
            {
                "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                "TMEM_FAKE_FZF_COUNTER": str(counter),
            },
        ):
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["shell-ui"]), 0)
        fields = output.getvalue().rstrip("\n").split("\t")
        self.assertEqual(fields[0], "execute")
        self.assertEqual(base64.b64decode(fields[1]).decode(), "echo via-details")
        self.assertEqual(counter.read_text(), "2")

    def test_doctor_runs_without_optional_fzf(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(["doctor"])
        self.assertIn(status, (0, 1))
        self.assertIn("Database:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
