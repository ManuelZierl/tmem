from __future__ import annotations

import contextlib
import io
import os
import shlex
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from tmem.terminal_ui import prompt_text, run_fzf

try:
    import pexpect
except ImportError:  # pragma: no cover
    pexpect = None


class TerminalUiTests(unittest.TestCase):
    @unittest.skipIf(shutil.which("fzf") is None, "requires fzf")
    def test_fzf_search_matches_visible_fields(self) -> None:
        rows = [
            "m:1\t★ catfile  cat {{file}}",
            "h:2\t11:19 cat LICENSE",
        ]
        with patch.dict(os.environ, {"FZF_DEFAULT_OPTS": "--filter=c"}):
            result = run_fzf(rows, header="test", expect=())
        self.assertIsNotNone(result)
        self.assertCountEqual(result.rows, rows)

        with patch.dict(os.environ, {"FZF_DEFAULT_OPTS": "--filter=m:1"}):
            hidden_id_result = run_fzf(rows, header="test", expect=())
        self.assertIsNone(hidden_id_result)

    def test_fallback_prompt_keeps_stdout_clean(self) -> None:
        output = io.StringIO()
        error = io.StringIO()
        with patch("tmem.terminal_ui._open_tty", return_value=None):
            with patch("builtins.input", return_value="value"):
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    self.assertEqual(prompt_text("Title", "Name"), "value")
        self.assertEqual(output.getvalue(), "")
        self.assertIn("Name", error.getvalue())

    @unittest.skipIf(pexpect is None, "requires pexpect")
    def test_prompt_supports_arrow_key_line_editing(self) -> None:
        repository = Path(__file__).parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repository / "src")
        environment["TERM"] = "xterm-256color"
        code = (
            "from tmem.terminal_ui import prompt_text; "
            "value = prompt_text('Edit', 'Value', allow_empty=False); "
            "print('__VALUE__' + str(value))"
        )
        child = pexpect.spawn(
            sys.executable,
            ["-c", code],
            env=environment,
            encoding="utf-8",
            timeout=8,
        )
        try:
            child.expect("Value: ")
            child.send("ab")
            child.send("\x1b[D")
            child.sendline("X")
            child.expect("__VALUE__aXb")
            child.expect(pexpect.EOF)
        finally:
            if child.isalive():
                child.terminate(force=True)

    @unittest.skipIf(pexpect is None, "requires pexpect")
    def test_terminal_subprocess_output_is_not_captured_as_protocol(self) -> None:
        repository = Path(__file__).parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repository / "src")
        environment["TERM"] = "xterm-256color"
        code = (
            "from tmem.terminal_ui import run_on_terminal; "
            "run_on_terminal(['bash', '-c', 'printf __EDITOR_OUTPUT__']); "
            "print('protocol')"
        )
        child = pexpect.spawn(
            "/bin/bash",
            ["--noprofile", "--norc", "-i"],
            env=environment,
            encoding="utf-8",
            timeout=8,
        )
        try:
            child.sendline(
                f"value=$({shlex.quote(sys.executable)} -c {shlex.quote(code)}); "
                "printf '__CAPTURED__%s\\n' \"$value\""
            )
            child.expect("__EDITOR_OUTPUT__")
            child.expect("__CAPTURED__protocol")
            child.sendline("exit")
            child.close()
        finally:
            if child.isalive():
                child.terminate(force=True)


if __name__ == "__main__":
    unittest.main()
