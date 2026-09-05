"""Bundle the canonical shell scripts in wheels, without maintaining copies."""
from pathlib import Path
from shutil import copy2
from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def run(self):
        super().run()
        destination = Path(self.build_lib) / "tmem" / "shell"
        destination.mkdir(parents=True, exist_ok=True)
        for path in Path("shell").glob("tmem.*"):
            copy2(path, destination / path.name)


setup(cmdclass={"build_py": BuildPy})
