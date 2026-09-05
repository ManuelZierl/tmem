from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

try:
    import pexpect
except ImportError:  # pragma: no cover
    pexpect = None

if os.name == "nt":
    pexpect = None


@unittest.skipIf(pexpect is None or shutil.which("bash") is None, "requires Bash and pexpect")
class ShellIntegrationTests(unittest.TestCase):
    def test_resolved_command_changes_current_shell_and_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            records = root / "records"
            core = bin_dir / "tmem-core"
            core.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    case "$1" in
                      memory-exists) exit 0 ;;
                      shell-run)
                        script=$(printf 'cd /tmp' | base64 | tr -d '\\n')
                        display=$(printf 'cd /tmp' | base64 | tr -d '\\n')
                        printf 'execute\\t%s\\t%s\\t1\\n' "$script" "$display"
                        ;;
                      record)
                        cat >> {shlex_quote(str(records))}
                        printf '\\n---\\n' >> {shlex_quote(str(records))}
                        ;;
                      *) exit 0 ;;
                    esac
                    """
                ),
                encoding="utf-8",
            )
            core.chmod(0o755)
            environment = os.environ.copy()
            environment["TMEM_INSTALL_SHELL"] = "bash"
            environment["PATH"] = str(bin_dir) + os.pathsep + environment["PATH"]
            environment["TERM"] = "xterm-256color"
            shell_file = Path(__file__).parents[1] / "shell" / "tmem.bash"

            child = pexpect.spawn(
                "/bin/bash",
                ["--noprofile", "--norc", "-i"],
                env=environment,
                encoding="utf-8",
                timeout=8,
            )
            try:
                child.sendline(
                    f"source {shlex_quote(str(shell_file))}; "
                    "PS1='__TMEM_PROMPT__ '; PS2='__TMEM_CONT__ '; echo __READY__"
                )
                child.expect("__READY__\\r\\n")
                child.expect("__TMEM_PROMPT__ ")

                child.sendline("tmem run demo")
                child.expect("__TMEM_PROMPT__ cd /tmp\\r\\n")
                child.expect("__TMEM_PROMPT__ ")

                child.sendline("printf '__PWD__%s\\n' \"$PWD\"")
                child.expect("__PWD__/tmp\\r\\n")
                child.expect("__TMEM_PROMPT__ ")
                child.sendline("exit")
                child.close()
            finally:
                if child.isalive():
                    child.terminate(force=True)

            recorded = records.read_text(encoding="utf-8")
            self.assertIn("cd /tmp", recorded)
            self.assertNotIn("tmem run demo", recorded)

    def test_installed_group_runs_in_calling_shell(self) -> None:
        repository = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            environment = os.environ.copy()
            environment["TMEM_INSTALL_SHELL"] = "bash"
            environment["HOME"] = str(home)
            environment["TERM"] = "xterm-256color"
            environment["TMEM_INSTALL_BASHRC"] = str(home / ".bashrc")
            install = subprocess.run(
                [str(repository / "install.sh")],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=15,
                check=True,
            )
            self.assertIn("Installed tmem", install.stdout)
            environment["PATH"] = str(home / ".local/bin") + os.pathsep + environment["PATH"]

            create = subprocess.run(
                [
                    str(home / ".local/bin/tmem"),
                    "group",
                    "shell-state",
                    "--",
                    "cd /tmp",
                    ":::",
                    "export TMEM_GROUP_VALUE=works",
                ],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=True,
            )
            self.assertEqual(create.stdout, "")

            shell_file = home / ".config/tmem/tmem.bash"
            child = pexpect.spawn(
                "/bin/bash",
                ["--noprofile", "--norc", "-i"],
                env=environment,
                encoding="utf-8",
                timeout=10,
            )
            try:
                child.sendline(
                    f"source {shlex_quote(str(shell_file))}; "
                    "PS1='__REAL_PROMPT__ '; PS2='__REAL_CONT__ '; echo __READY__"
                )
                child.expect("__READY__\r\n")
                child.expect("__REAL_PROMPT__ ")
                child.sendline("tmem run shell-state")
                child.expect("__REAL_PROMPT__ cd /tmp &&\r\n")
                child.expect("__REAL_CONT__ export TMEM_GROUP_VALUE=works\r\n")
                child.expect("__REAL_PROMPT__ ")
                child.sendline('printf \'__STATE__%s:%s\\n\' "$PWD" "$TMEM_GROUP_VALUE"')
                child.expect("__STATE__/tmp:works\r\n")
                child.expect("__REAL_PROMPT__ ")
                for _ in range(2):
                    child.sendline("echo __REPEAT__")
                    child.expect("[\r\n]__REPEAT__\r\n")
                    child.expect("__REAL_PROMPT__ ")
                child.sendline("")
                child.expect("__REAL_PROMPT__ ")
                child.sendline("false")
                child.expect("__REAL_PROMPT__ ")
                child.sendline("printf '__FALSE_STATUS__%s\\n' \"$?\"")
                child.expect("__FALSE_STATUS__1\r\n")
                child.expect("__REAL_PROMPT__ ")
                child.sendline("exit")
                child.close()
            finally:
                if child.isalive():
                    child.terminate(force=True)

            import sys
            sys.path.insert(0, str(repository / "src"))
            from tmem.db import TmemDB

            with TmemDB(home / ".local/share/tmem/tmem.db") as db:
                memory = db.get_memory("shell-state")
                self.assertIsNotNone(memory)
                assert memory is not None
                self.assertEqual(memory.run_count, 1)
                commands = [entry.command for entry in db.list_history()]
                self.assertTrue(
                    any(
                        "cd /tmp" in command and "export TMEM_GROUP_VALUE=works" in command
                        for command in commands
                    )
                )
                self.assertFalse(any("tmem run shell-state" in command for command in commands))
                self.assertFalse(any(command.startswith("_tmem_") for command in commands))
                self.assertEqual(commands.count("echo __REPEAT__"), 2)
                self.assertIn("false", commands, commands)
                failed = next(entry for entry in db.list_history() if entry.command == "false")
                self.assertEqual(
                    failed.exit_code,
                    1,
                    [(entry.command, entry.exit_code) for entry in db.list_history()],
                )

    def test_multiline_command_is_recorded_in_full(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            records = root / "records"
            core = bin_dir / "tmem-core"
            core.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    if [[ $1 == record ]]; then
                        cat >> {shlex_quote(str(records))}
                        printf '\n---\n' >> {shlex_quote(str(records))}
                    fi
                    """
                ),
                encoding="utf-8",
            )
            core.chmod(0o755)
            environment = os.environ.copy()
            environment["TMEM_INSTALL_SHELL"] = "bash"
            environment["PATH"] = str(bin_dir) + os.pathsep + environment["PATH"]
            environment["TERM"] = "xterm-256color"
            shell_file = Path(__file__).parents[1] / "shell" / "tmem.bash"
            child = pexpect.spawn(
                "/bin/bash",
                ["--noprofile", "--norc", "-i"],
                env=environment,
                encoding="utf-8",
                timeout=8,
            )
            try:
                child.sendline(
                    f"source {shlex_quote(str(shell_file))}; "
                    "PS1='__PROMPT__ '; PS2='__CONT__ '; echo __READY__"
                )
                child.expect("__READY__\r\n")
                child.expect("__PROMPT__ ")
                child.sendline("printf '__ONE__' && " + "\\")
                child.expect("__CONT__ ")
                child.sendline("printf '__TWO__'")
                child.expect("__ONE____TWO__")
                child.expect("__PROMPT__ ")
                child.sendline("exit")
                child.close()
            finally:
                if child.isalive():
                    child.terminate(force=True)

            recorded = records.read_text(encoding="utf-8")
            self.assertIn("printf '__ONE__'", recorded)
            self.assertIn("printf '__TWO__'", recorded)
            command_blocks = [block for block in recorded.split("\n---\n") if "__ONE__" in block]
            self.assertEqual(len(command_blocks), 1)
            self.assertIn("printf '__TWO__'", command_blocks[0])

    def test_same_memory_name_resolves_by_current_directory(self) -> None:
        repository = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            project_a = home / "project-a"
            project_b = home / "project-b"
            other = home / "other"
            for path in (project_a, project_b, other):
                path.mkdir()
            environment = os.environ.copy()
            environment["TMEM_INSTALL_SHELL"] = "bash"
            environment["HOME"] = str(home)
            environment["TERM"] = "xterm-256color"
            environment["TMEM_INSTALL_BASHRC"] = str(home / ".bashrc")
            subprocess.run(
                [str(repository / "install.sh")],
                env=environment,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
            )
            environment["PATH"] = str(home / ".local/bin") + os.pathsep + environment["PATH"]
            launcher = str(home / ".local/bin/tmem")
            subprocess.run(
                [launcher, "save", "watch", "--", "export TMEM_SCOPE_VALUE=global"],
                cwd=home,
                env=environment,
                check=True,
                timeout=10,
            )
            subprocess.run(
                [
                    launcher,
                    "save",
                    "catfile",
                    "--",
                    "printf '__CATFILE__%s\\n' {{file}}",
                ],
                cwd=home,
                env=environment,
                check=True,
                timeout=10,
            )
            subprocess.run(
                [launcher, "save", "--here", "watch", "--", "export TMEM_SCOPE_VALUE=a"],
                cwd=project_a,
                env=environment,
                check=True,
                timeout=10,
            )
            subprocess.run(
                [launcher, "save", "--here", "watch", "--", "export TMEM_SCOPE_VALUE=b"],
                cwd=project_b,
                env=environment,
                check=True,
                timeout=10,
            )

            shell_file = home / ".config/tmem/tmem.bash"
            child = pexpect.spawn(
                "/bin/bash",
                ["--noprofile", "--norc", "-i"],
                env=environment,
                encoding="utf-8",
                timeout=10,
            )
            try:
                child.sendline(
                    f"source {shlex_quote(str(shell_file))}; "
                    "PS1='__SCOPE_PROMPT__ '; echo __READY__"
                )
                child.expect("__READY__\r\n")
                child.expect("__SCOPE_PROMPT__ ")

                child.sendline("tmem catfile README.md")
                child.expect("__CATFILE__README.md\r\n")
                child.expect("__SCOPE_PROMPT__ ")

                for path, invocation, expected in (
                    (project_a, "tmem run watch", "a"),
                    (project_b, "tmem watch", "b"),
                    (other, "tmem run watch", "global"),
                ):
                    child.sendline(f"cd {shlex_quote(str(path))}")
                    child.expect("__SCOPE_PROMPT__ ")
                    child.sendline(invocation)
                    child.expect("__SCOPE_PROMPT__ ")
                    child.sendline("printf '__SCOPE_VALUE__%s\\n' \"$TMEM_SCOPE_VALUE\"")
                    child.expect(f"__SCOPE_VALUE__{expected}\\r\\n")
                    child.expect("__SCOPE_PROMPT__ ")
                child.sendline("exit")
                child.close()
            finally:
                if child.isalive():
                    child.terminate(force=True)


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


if __name__ == "__main__":
    unittest.main()
