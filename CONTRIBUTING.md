# Contributing to tmem

Small, focused fixes and documentation improvements are welcome. Open an issue
before starting a broad behavior or storage-format change so the intended
contract can be agreed first.

## Development setup

The full test suite requires Linux, Bash, `fzf`, Python 3.10 or newer, and the
development dependencies declared in `pyproject.toml`.

On Ubuntu or Debian, install the system dependencies first:

```bash
sudo apt install python3-venv fzf
```

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
make test
make build
```

`make test` runs the Python unit and pseudo-terminal integration tests and
checks the Bash scripts for syntax errors. Tests that require a real terminal
use `pexpect` and should not be skipped in a complete verification run.

## Architecture constraints

- `shell/tmem.bash` owns changes to the calling shell, including `cd`, `source`,
  exports, history replacement, and execution.
- The Python core owns persistence, selection, templates, and command
  resolution. For shell execution it writes one tab-separated, base64-encoded
  protocol line to stdout.
- Interactive prompts and editors must use `/dev/tty`; extra stdout output can
  corrupt the shell execution protocol.
- SQLite schema changes must preserve existing histories and saved memories.
- Installation changes must not overwrite or remove files that are not marked
  as tmem-owned.

## Pull requests

- Add or update tests for observable behavior changes.
- Keep unrelated cleanup out of the same change.
- Update the README when installation, configuration, or user interaction
  changes.
- Never commit command-history databases, credentials, local OpenCode state,
  virtual environments, or generated build files.

For vulnerabilities or changes affecting command execution and sensitive
history data, follow [SECURITY.md](SECURITY.md) instead of opening a public
issue with exploit details.
