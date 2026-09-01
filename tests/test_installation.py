from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


class InstallationTests(unittest.TestCase):
    def test_custom_paths_are_quoted_and_uninstall_preserves_unrelated_files(self) -> None:
        repository = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir()
            app_dir = root / 'app" shared'
            bin_dir = root / 'bin" tools'
            config_dir = root / 'config" files'
            bashrc = root / "custom bashrc"
            app_dir.mkdir()
            sentinel = app_dir / "keep-me"
            sentinel.write_text("owned by user", encoding="utf-8")

            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(home),
                    "TMEM_INSTALL_APP_DIR": str(app_dir),
                    "TMEM_INSTALL_BIN_DIR": str(bin_dir),
                    "TMEM_INSTALL_CONFIG_DIR": str(config_dir),
                    "TMEM_INSTALL_BASHRC": str(bashrc),
                }
            )
            subprocess.run(
                [str(repository / "install.sh")],
                env=environment,
                text=True,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
            )
            for launcher in (bin_dir / "tmem", bin_dir / "tmem-core"):
                subprocess.run(["bash", "-n", str(launcher)], check=True, timeout=5)

            shell_check = subprocess.run(
                [
                    "bash",
                    "--noprofile",
                    "--norc",
                    "-c",
                    f"source {shlex.quote(str(bashrc))}; type -t tmem; \"$TMEM_CORE\" --version",
                ],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                timeout=10,
            )
            self.assertIn("function", shell_check.stdout)
            self.assertIn("tmem 0.1.0", shell_check.stdout)

            subprocess.run(
                [str(repository / "uninstall.sh")],
                env=environment,
                text=True,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
            )
            self.assertTrue(sentinel.exists())
            self.assertFalse((app_dir / "tmem").exists())
            self.assertFalse((bin_dir / "tmem").exists())
            self.assertNotIn("tmem.bash", bashrc.read_text(encoding="utf-8"))

    def test_uninstall_preserves_preexisting_bashrc_source_line(self) -> None:
        repository = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bashrc = home / ".bashrc"
            source_line = (
                '[[ -f "$HOME/.config/tmem/tmem.bash" ]] '
                '&& source "$HOME/.config/tmem/tmem.bash"'
            )
            bashrc.write_text(
                "# tmem terminal command memory\n" + source_line + "\n# user content\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["HOME"] = str(home)
            environment["TMEM_INSTALL_BASHRC"] = str(bashrc)
            subprocess.run(
                [str(repository / "install.sh")],
                env=environment,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
            )
            subprocess.run(
                [str(repository / "uninstall.sh")],
                env=environment,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
            )
            remaining = bashrc.read_text(encoding="utf-8")
            self.assertIn(source_line, remaining)
            self.assertIn("# user content", remaining)

    def test_install_refuses_to_overwrite_unknown_target_files(self) -> None:
        repository = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bin_dir = home / ".local/bin"
            config_dir = home / ".config/tmem"
            bin_dir.mkdir(parents=True)
            config_dir.mkdir(parents=True)
            targets = {
                bin_dir / "tmem": "user launcher",
                bin_dir / "tmem-core": "user core",
                config_dir / "tmem.bash": "user integration",
            }
            for path, content in targets.items():
                path.write_text(content, encoding="utf-8")
            environment = os.environ.copy()
            environment["HOME"] = str(home)
            result = subprocess.run(
                [str(repository / "install.sh")],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Refusing to overwrite", result.stderr)
            for path, content in targets.items():
                self.assertEqual(path.read_text(encoding="utf-8"), content)
            subprocess.run(
                [str(repository / "uninstall.sh")],
                env=environment,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
            )
            for path, content in targets.items():
                self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_uninstall_preserves_preexisting_bashrc_comment(self) -> None:
        repository = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bashrc = home / ".bashrc"
            comment = "# tmem terminal command memory"
            bashrc.write_text(comment + "\n# user content\n", encoding="utf-8")
            environment = os.environ.copy()
            environment["HOME"] = str(home)
            environment["TMEM_INSTALL_BASHRC"] = str(bashrc)
            subprocess.run(
                [str(repository / "install.sh")],
                env=environment,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
            )
            subprocess.run(
                [str(repository / "uninstall.sh")],
                env=environment,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
            )
            remaining = bashrc.read_text(encoding="utf-8")
            self.assertEqual(remaining.count(comment), 1)
            self.assertIn("# user content", remaining)


if __name__ == "__main__":
    unittest.main()
