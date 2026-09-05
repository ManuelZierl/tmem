from __future__ import annotations

import base64
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tmem.cli import main
from tmem.config import config_dir, data_dir
from tmem.db import TmemDB
from tmem.shells import PS_SINGLE_QUOTES, compatible_shell, quote_argument
from tmem.templates import build_script, render_template, shell_tokens
from tmem.tui import ItemRef, TmemUI

ROOT = Path(__file__).resolve().parents[1]
PW = shutil.which('pwsh')
ZSH = shutil.which('zsh')


def environment(directory: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(PYTHONPATH=str(ROOT / 'src'), PYTHONUTF8='1',
               TMEM_DB=str(Path(directory) / 'history.db'), TMEM_CONFIG_DIR=directory,
               TMEM_TEST_PYTHON=sys.executable, TMEM_SHELL='bash')
    return env


class PortabilityTests(unittest.TestCase):
    def test_native_windows_paths_and_explicit_overrides(self):
        with patch.dict(os.environ, {'LOCALAPPDATA': '/local', 'APPDATA': '/roaming'}, clear=True):
            with patch('tmem.config.sys.platform', 'win32'):
                self.assertEqual(data_dir(), Path('/local/tmem'))
                self.assertEqual(config_dir(), Path('/roaming/tmem'))
                with patch.dict(os.environ, {'TMEM_DATA_DIR': '/custom', 'XDG_CONFIG_HOME': '/xdg'}):
                    self.assertEqual(data_dir(), Path('/custom'))
                    self.assertEqual(config_dir(), Path('/xdg/tmem'))

    def test_shell_resources_available_without_opening_database(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, environment(directory)):
                for shell in ('bash', 'zsh', 'powershell'):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(main(['init', shell]), 0)
                    self.assertIn('tmem', output.getvalue())
                self.assertFalse(Path(directory, 'history.db').exists())

    def test_powershell_quoting_preserves_literal_values(self):
        for value in ("", "O'Brien", 'a b', r'C:\Users\name with spaces', '€東京',
                      '$(Remove-Item x); $env:SECRET', 'line\none', '`"&|', '‘’‚‛; evil'):
            with self.subTest(value=value):
                self.assertEqual(quote_argument(value, 'powershell'), "'" + "".join(c * 2 if c in PS_SINGLE_QUOTES else c for c in value) + "'")
        with self.assertRaises(ValueError):
            quote_argument('\0', 'powershell')

    def test_placeholders_inside_quotes_fail_closed(self):
        for shell in ('bash', 'zsh', 'powershell'):
            for template in ('echo "{{x}}"', "echo '{{x}}'"):
                with self.subTest(shell=shell, template=template):
                    with self.assertRaises(ValueError):
                        render_template(template, {'x': "'; evil"}, shell=shell)

    def test_powershell_tokens_preserve_paths_and_apostrophes(self):
        tokens = shell_tokens("Get-Item 'C:\\Users\\O''Brien\\a b.txt'", shell='powershell')
        self.assertEqual(tokens[-1].value, "C:\\Users\\O'Brien\\a b.txt")
        self.assertNotIn('$env:HOME', [t.value for t in shell_tokens('echo $env:HOME', shell='powershell')])

    def test_powershell_groups_dot_source_steps(self):
        self.assertEqual(build_script(['$x = 1', '$x += 1'], shell='powershell'),
                         '. {\n$x = 1\n} &&\n. {\n$x += 1\n}')
        self.assertEqual(build_script(['first', 'second'], False, shell='powershell'), 'first\nsecond')

    def test_mismatched_shell_history_and_memories_cannot_execute(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TMEM_SHELL': 'powershell'}):
            with TmemDB(Path(directory) / 'history.db') as db:
                memory = db.create_memory('unix', ['echo {{x}}'], shell='bash')
                history = db.record_history('echo unix', '', 0, 1, 2, '', '', shell='bash')
                ui = TmemUI(db)
                self.assertEqual(ui._main_rows(), [])
                with self.assertRaises(ValueError):
                    ui.resolve_memory(memory)
                with self.assertRaises(ValueError):
                    ui._execution_for_refs([ItemRef('h', history)])
                db.create_memory('native', ['Write-Output ok'])
                self.assertIn('native', ui._main_rows()[0])
                self.assertEqual(db.get_memory('native').shell, 'powershell')
        self.assertTrue(compatible_shell('bash', 'zsh'))
        self.assertFalse(compatible_shell('powershell', 'bash'))

    def test_history_import_formats_and_multiline_idempotence(self):
        cases = {
            'zsh': ': 1700000000:2;echo one\\\necho two\necho three\n',
            'powershell': 'Write-Output one`\nWrite-Output two\nWrite-Output three\n',
        }
        with tempfile.TemporaryDirectory() as directory:
            with TmemDB(Path(directory) / 'history.db') as db:
                for shell, text in cases.items():
                    path = Path(directory) / shell
                    path.write_text(text, encoding='utf-8-sig')
                    self.assertEqual(db.import_history(path, shell), (2, 0))
                    self.assertEqual(db.import_history(path, shell), (0, 2))
                entries = db.list_history()
                self.assertEqual({e.shell for e in entries}, {'zsh', 'powershell'})
                self.assertEqual(sum('\n' in e.command for e in entries), 2)

    @unittest.skipIf(os.name == 'nt', 'Unix installer')
    def test_bash_and_zsh_installations_coexist_and_uninstall_independently(self):
        with tempfile.TemporaryDirectory(prefix='tmem spaced ') as directory:
            home = Path(directory)
            env = environment(directory)
            env.update(HOME=directory, TMEM_INSTALL_APP_DIR=str(home / 'app'),
                       TMEM_INSTALL_CONFIG_DIR=str(home / 'config'), TMEM_INSTALL_BIN_DIR=str(home / 'bin'),
                       TMEM_INSTALL_BASHRC=str(home / 'bashrc'), TMEM_INSTALL_ZSHRC=str(home / 'zshrc'))
            for shell in ('bash', 'zsh', 'zsh'):
                subprocess.run(['bash', str(ROOT / 'install.sh'), '--shell', shell], env=env, check=True, capture_output=True)
            self.assertEqual((home / 'zshrc').read_text().count('# tmem terminal command memory'), 1)
            subprocess.run(['bash', str(ROOT / 'uninstall.sh'), '--shell', 'zsh'], env=env, check=True, capture_output=True)
            self.assertTrue((home / 'app/tmem/cli.py').exists())
            self.assertTrue((home / 'config/tmem.bash').exists())
            subprocess.run(['bash', str(ROOT / 'uninstall.sh'), '--shell', 'bash'], env=env, check=True, capture_output=True)
            self.assertFalse((home / 'app/tmem').exists())
            self.assertTrue((home / 'config/config.json').exists())

    @unittest.skipIf(os.name == 'nt', 'Bash clock compatibility')
    def test_bsd_date_literal_nanoseconds_falls_back_to_numeric_timestamp(self):
        script = f'''source {str(ROOT / 'shell/tmem.bash')!r}
        date() {{ if [[ $1 == +%s%3N ]]; then printf 17000000003N; else printf 1700000000; fi; }}
        _tmem_now_ms'''
        result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), '1700000000000')

    @unittest.skipUnless(PW, 'requires native PowerShell 7.3+')
    def test_native_powershell_adapter(self):
        with tempfile.TemporaryDirectory(prefix="tmem O'Brien ") as directory:
            env = environment(directory)
            core = Path(directory) / 'core.ps1'
            core.write_text('& $env:TMEM_TEST_PYTHON -m tmem @args\n', encoding='utf-8')
            env['TMEM_CORE'] = str(core)
            result = subprocess.run([PW, '-NoProfile', '-NonInteractive', '-File', str(ROOT / 'tests/powershell_smoke.ps1')],
                                    env=env, text=True, encoding='utf-8', capture_output=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('POWERSHELL_OK', result.stdout)
            with TmemDB(Path(directory) / 'history.db') as db:
                entries = db.list_history()
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0].shell, 'powershell')
                self.assertEqual(entries[0].exit_code, 7)
                self.assertIn('recorded', entries[0].command)

    @unittest.skipUnless(os.name == 'nt' and PW, 'requires Windows PowerShell installer')
    def test_native_windows_install_and_uninstall(self):
        with tempfile.TemporaryDirectory(prefix="tmem O'Brien ") as directory:
            home = Path(directory)
            env = environment(directory)
            env.update(TMEM_INSTALL_APP_DIR=str(home / 'app'), TMEM_INSTALL_CONFIG_DIR=str(home / 'config'),
                       TMEM_INSTALL_BIN_DIR=str(home / 'bin'), TMEM_INSTALL_PROFILE=str(home / 'profile.ps1'))
            profile = home / 'profile.ps1'
            profile.write_text('# existing user content\n', encoding='utf-8')
            for _ in range(2):
                result = subprocess.run([PW, '-NoProfile', '-NonInteractive', '-File', str(ROOT / 'install.ps1'), '-Python', sys.executable],
                                        env=env, capture_output=True, text=True, encoding='utf-8', timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(profile.read_text().count('# >>> tmem >>>'), 1)
            result = subprocess.run([PW, '-NoProfile', '-NonInteractive', '-File', str(home / 'bin/tmem-core.ps1'), 'init', 'powershell'],
                                    env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('PSConsoleHostReadLine', result.stdout)
            (home / 'history.db').write_bytes(b'keep history')
            (home / 'app/unrelated').write_text('keep unrelated')
            for _ in range(2):
                result = subprocess.run([PW, '-NoProfile', '-NonInteractive', '-File', str(ROOT / 'uninstall.ps1')],
                                        env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('# existing user content', profile.read_text())
            self.assertNotIn('# >>> tmem >>>', profile.read_text())
            self.assertFalse((home / 'app/tmem').exists())
            self.assertEqual((home / 'history.db').read_bytes(), b'keep history')
            self.assertTrue((home / 'app/unrelated').exists())

    @unittest.skipUnless(ZSH, 'requires zsh')
    def test_zsh_scope_group_status_and_protocol(self):
        with tempfile.TemporaryDirectory(prefix='tmem zsh ') as directory:
            env = environment(directory)
            core = Path(directory) / 'core'
            import shlex
            core.write_text('#!/usr/bin/env bash\nexec ' + shlex.quote(sys.executable) + ' -m tmem "$@"\n')
            core.chmod(0o700)
            env['TMEM_CORE'] = str(core)
            with patch.dict(os.environ, {'TMEM_SHELL': 'zsh'}):
                with TmemDB(Path(directory) / 'history.db') as db:
                    db.create_memory('state', [f'cd {shlex.quote(directory)}', 'export TMEM_TEST_VALUE=ok', 'false', 'touch should-not-exist'])
            script = f'''source {shlex.quote(str(ROOT / 'shell/tmem.zsh'))}
            tmem run state
            code=$?
            [[ $code == 1 && $TMEM_TEST_VALUE == ok && $PWD == {shlex.quote(directory)} ]] || exit 9
            [[ ! -e should-not-exist ]] || exit 10
            print -- ZSH_OK'''
            result = subprocess.run([ZSH, '-f', '-c', script], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('ZSH_OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
