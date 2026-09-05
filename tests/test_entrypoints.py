"""Exercise user-facing launch paths, not only imported Python functions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tmem.templates import build_script

ROOT = Path(__file__).resolve().parents[1]
PW = shutil.which("pwsh")


def environment(directory: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        PYTHONPATH=str(ROOT / "src"),
        TMEM_DB=str(Path(directory) / "source.db"),
        TMEM_CONFIG_DIR=directory,
        TMEM_SHELL="bash",
        TMEM_TEST_PYTHON=sys.executable,
        # Test UTF-8 transfer even with a non-UTF-8 inherited pipe encoding.
        PYTHONUTF8="0",
        PYTHONIOENCODING="cp1252",
    )
    return env


class EntrypointTests(unittest.TestCase):
    def run_command(self, command: list[str], env: dict[str, str], **kwargs):
        result = subprocess.run(
            command, env=env, text=True, encoding="utf-8", capture_output=True,
            timeout=30, **kwargs,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_module_entrypoint_round_trip_with_utf8_pipes(self):
        with tempfile.TemporaryDirectory() as directory:
            env = environment(directory)
            command = [sys.executable, "-m", "tmem"]
            self.run_command(command + ["save", "unicode", "--", "echo '€東京'"], env)
            exported = self.run_command(command + ["export"], env)
            document = json.loads(exported.stdout)
            self.assertEqual(document["memories"][0]["commands"], ["echo '€東京'"])
            env["TMEM_DB"] = str(Path(directory) / "target.db")
            self.run_command(command + ["import", "-"], env, input=exported.stdout)
            restored = self.run_command(command + ["export"], env)
            self.assertEqual(json.loads(restored.stdout), document)

    @unittest.skipIf(os.name == "nt", "Unix installer; native Windows installer has its own smoke test")
    def test_installed_unix_shells_route_transfer_verbs(self):
        for shell in ("bash", "zsh"):
            executable = shutil.which(shell)
            if executable is None:
                continue  # zsh is installed on the Linux/macOS CI runners.
            with self.subTest(shell=shell), tempfile.TemporaryDirectory(prefix="tmem entry ") as directory:
                home = Path(directory)
                env = environment(directory)
                env.update(
                    HOME=directory, TMEM_INSTALL_APP_DIR=str(home / "app"),
                    TMEM_INSTALL_BIN_DIR=str(home / "bin"),
                    TMEM_INSTALL_CONFIG_DIR=str(home / "config"),
                    TMEM_INSTALL_BASHRC=str(home / "bashrc"),
                    TMEM_INSTALL_ZSHRC=str(home / "zshrc"),
                    TMEM_SHELL=shell,
                    TMEM_TEST_INTEGRATION=str(home / "config" / f"tmem.{shell}"),
                )
                env.pop("TMEM_CORE", None)
                self.run_command(["bash", str(ROOT / "install.sh"), "--shell", shell], env)
                # The executable installed from the source tree uses python -m.
                self.run_command([str(home / "bin/tmem"), "save", "export", "--", "echo do-not-run"], env)
                invocation = [executable, "-c", 'source "$TMEM_TEST_INTEGRATION"; tmem export']
                exported = self.run_command(invocation, env)
                document = json.loads(exported.stdout)
                self.assertEqual(document["memories"][0]["name"], "export")
                env["TMEM_DB"] = str(home / "target.db")
                self.run_command(
                    [executable, "-c", 'source "$TMEM_TEST_INTEGRATION"; tmem import -'],
                    env, input=exported.stdout,
                )
                self.assertEqual(json.loads(self.run_command(invocation, env).stdout), document)

    @unittest.skipUnless(PW, "requires PowerShell 7.3+")
    def test_powershell_top_level_group_status_and_scope(self):
        native_failure = '& $env:TMEM_TEST_PYTHON -c "raise SystemExit(7)"'
        cases = [
            ("first-failure", [native_failure, "$before = 1"], True, False, 7, None, None),
            ("middle-failure", ["$before = 1", native_failure, "$after = 1"], True, False, 7, 1, None),
            ("last-failure", ["$before = 1", native_failure], True, False, 7, 1, None),
            ("success-stale-native-code", ["$before = 1", "$after = 1"], True, True, 91, 1, 1),
            ("continue-after-failure", [native_failure, "$after = 1"], False, True, 7, None, 1),
            ("cmdlet-failure", ["Write-Error failed -ErrorAction SilentlyContinue", "$after = 1"], True, False, 91, None, None),
        ]
        probe = '''
$ok = $?
$nativeCode = $LASTEXITCODE
@{ok=$ok; code=$nativeCode; before=$before; after=$after} | ConvertTo-Json -Compress
'''
        with tempfile.TemporaryDirectory() as directory:
            for label, commands, stop, ok, code, before, after in cases:
                with self.subTest(case=label):
                    # No dot-sourced test-only wrapper around the generated
                    # statement list. This is the code a prompt would execute.
                    script = "$LASTEXITCODE = 91\n" + build_script(commands, stop, shell="powershell") + probe
                    result = self.run_command([PW, "-NoProfile", "-NonInteractive", "-Command", script], environment(directory))
                    self.assertEqual(json.loads(result.stdout), {"ok": ok, "code": code, "before": before, "after": after})


if __name__ == "__main__":
    unittest.main()
