# tmem

`tmem` is persistent, fuzzy-searchable terminal command memory for Bash.

> **Important:** `tmem` records complete shell commands and executes selected
> commands in the current shell. Review the [privacy and security](#privacy-and-security)
> section before using it with sensitive commands.

It behaves like an expanded `Ctrl-R`:

- every completed command is recorded immediately with its directory, exit code, timestamp, duration, host, and shell session;
- `tmem` opens an interactive fuzzy-searchable history;
- **Enter runs** the selected command in the current shell;
- **Right Arrow opens details and all available actions** for the selected item;
- useful commands can be saved as named memories;
- memories can contain parameters whose recent values are remembered;
- multiple historical commands can be saved and run as an ordered command group.

A normal terminal session looks like this:

```text
$ tmem

  ★ service-logs        docker compose logs worker
  ▦ release [2 steps]   git tag {{tag}}  →  git push origin {{tag}}
  16:42:09 ✓ ~/demo-app docker compose ps
> 16:40:31 ✓ ~/demo-app docker compose logs worker

Enter run  ·  → details/actions  ·  Tab mark multiple  ·  Esc close
```

Selecting `docker compose logs worker` with Enter redraws the terminal as:

```text
$ docker compose logs worker
```

The visible command and Bash history contain the resolved command—not `tmem`—and it executes in the calling shell.

## Requirements

`tmem` 0.1 supports interactive Bash on Linux. Ubuntu is the tested platform;
other Linux distributions may work but are not currently covered by CI.

- Python 3.10 or newer;
- Bash and standard command-line utilities, including `base64`;
- [`fzf`](https://github.com/junegunn/fzf) for the interactive interface.

The Python runtime uses only the standard library. `tmem` does not require a
server or network account.

## Quick start

Install the dependencies on Ubuntu or Debian:

```bash
sudo apt update
sudo apt install python3 fzf
```

Then clone and install `tmem`:

```bash
git clone https://github.com/ManuelZierl/tmem.git
cd tmem
./install.sh
source ~/.config/tmem/tmem.bash

tmem doctor
```

The installer:

- copies the Python application to `~/.local/share/tmem/app`;
- installs `tmem` and `tmem-core` under `~/.local/bin`;
- installs the Bash integration at `~/.config/tmem/tmem.bash`;
- adds one guarded `source` line to `~/.bashrc`.

Installed files carry ownership markers. The installer refuses to overwrite an unrelated file at one of its target paths, and the uninstaller removes only files and Bash configuration recorded as tmem-owned.

The supported interactive installation path is `install.sh`. Installing the
Python package alone provides `tmem-core` but does not install the required Bash
integration.

The interactive shell integration is essential. A standalone child process cannot change its parent shell, so `cd`, `source`, `export`, aliases, shell options, and similar commands require the Bash function installed by `install.sh`. The fallback executable refuses `tmem` and `tmem run …` rather than silently running them in a subprocess.

## Main interaction model

Run:

```bash
tmem
```

The main view has only three primary interactions:

```text
Type         fuzzy-search
Enter        run the selected item
Right Arrow  open its details and available actions
```

`Esc` goes back or closes a view. `Tab` marks multiple rows when building or running a group; this control is always displayed in the relevant view.

Typing `dcker` matches `docker`: interactive ranking is delegated to `fzf`, while `tmem search` uses a compatible subsequence matcher for non-interactive use.

### History command actions

Right Arrow on a history entry opens a selectable action list:

```text
Run
Edit before running
Save as global memory
Save as memory for this directory
Save as global parameterized memory
Save as parameterized memory for this directory
Create a global or directory group with this command…
Show all occurrences
Copy command
Delete from tmem history
```

No action depends on an undisclosed keyboard shortcut.

### Saved-memory actions

Right Arrow on a saved memory provides:

```text
Run
Edit resolved command before running
Edit name, directory, description, and commands in $EDITOR
Rename
Bind to current directory / Make global
Manage parameter defaults and remembered values
Add commands from history                 # groups
Toggle stop-on-failure mode               # groups
Copy command template
Delete memory
```

## Named memories

Save a selected history command through its Right Arrow menu, then run it with either form:

```bash
tmem run service-logs
tmem service-logs
```

The explicit `run` form is always available. The shorter form works unless the memory name collides with a built-in subcommand such as `search` or `list`.

Non-interactive creation is also available:

```bash
tmem save service-logs -- 'docker compose logs worker'
```

### Directory memories

Use `--here` when a memory only makes sense in the current directory:

```bash
cd ~/demo-app
tmem save --here watch -- \
  'watch -n 10 wc -l var/requests.ndjson'
```

The same name can have one global definition and separate definitions for several exact directories:

```bash
cd ~/project-a
tmem save --here watch -- 'watch ./project-a.log'

cd ~/project-b
tmem save --here watch -- 'watch ./project-b.log'
```

`tmem watch` and `tmem run watch` first look for a memory bound to the current directory, then fall back to a global memory with that name. Parent directories are not searched. Existing memories and saves without `--here` remain global.

Use the global version explicitly when a local memory shadows it:

```bash
tmem run --global watch
tmem show --global watch
tmem edit --global watch
tmem rm --global watch
```

`tmem list` shows every saved memory with `scope=global` or its bound directory. The main interactive view only shows memories available in the current directory and labels them `[global]` or `[here]`. From a memory's action menu, **Bind to current directory** and **Make global** change its scope. The JSON editor uses `"directory": null` for a global memory and an absolute path for a directory memory.

On first use after upgrading, existing databases are migrated automatically and all existing memories remain global.

## Parameters

Select **Save as parameterized memory**, then:

1. mark one or more whole shell tokens;
2. name each parameter;
3. save the memory.

For example, this history command:

```bash
kubectl logs deployment/api -n production
```

can become:

```text
kubectl logs {{workload}} -n {{namespace}}
```

Running it opens a selector for each parameter:

```bash
tmem run cluster-logs
```

Previously used values are shown as choices and remembered for later. Values can also be supplied directly:

```bash
tmem run cluster-logs deployment/worker staging
tmem run cluster-logs workload=deployment/worker namespace=staging
```

Positional values bind to placeholders in template order, so a memory containing `cat {{file}}` can be run as `tmem catfile README.md`. Named `parameter=value` arguments remain useful when skipping or reordering parameters; positional and named values can be mixed.

Parameter values are shell-quoted during rendering. The interactive parameterizer intentionally replaces complete shell tokens, which avoids accidental quoting errors.

## Command groups

Groups are ordered lists of commands. A typical release group could contain:

```text
1. git tag {{tag}}
2. git push origin {{tag}}
```

There are two TUI paths, each offering global and current-directory variants:

- mark multiple history rows with `Tab`, press Right Arrow, and select **Save as command group**;
- press Right Arrow on one command and select **Create a group with this command…**.

Commands selected from history are ordered by their original execution timestamps, oldest first. Groups stop at the first failing command by default, so the second command is not run if the first fails. This can be toggled from the group’s detail view.

Run a group normally:

```bash
tmem run release tag=v1.4.0
```

The group executes in the current shell. Therefore this is valid and leaves the caller inside `/tmp/project` with the virtual environment active:

```text
1. cd /tmp/project
2. source .venv/bin/activate
```

A group can also be created without the TUI by separating steps with `:::`:

```bash
tmem group release -- \
  'git tag {{tag}}' ::: \
  'git push origin {{tag}}'

# Bind the group to the current directory instead:
tmem group --here release -- \
  'git tag {{tag}}' ::: \
  'git push origin {{tag}}'
```

## Non-interactive history access

`tmem search` prints fuzzy matches and never runs them:

```bash
tmem search dcker
tmem search 'kubectl logs' --limit 20
tmem search docker --cwd "$PWD"
tmem search worker --failed
tmem search postgres --json
```

Additional views:

```bash
tmem failed
tmem today
tmem cwd
tmem stats
tmem list
tmem show release
```

## Import existing Bash history

Live tracking begins once the shell integration is loaded. Existing history can be imported separately:

```bash
tmem import-history
tmem import-history /path/to/another/.bash_history
```

Imports are idempotent. Ordinary Bash history does not contain reliable working-directory or exit-code metadata, so imported rows leave those fields unknown.

## Recording model

The Bash integration captures one user-entered command per prompt and records it after completion. It also records repetitions that Bash itself may omit due to `HISTCONTROL=ignoredups`. If another application already owns Bash's `DEBUG` trap, `tmem` deliberately leaves it untouched and uses a less complete prompt-time fallback; `tmem doctor` reports the active capture mode.

Each live row contains:

```text
command
working directory
exit code
start and finish time
duration
hostname
shell session ID
```

`tmem` records commands, not command output. It is not a terminal-session recorder such as `script` or asciinema.

The SQLite database is stored at:

```text
~/.local/share/tmem/tmem.db
```

The data directory is created with mode `0700` and the database with mode `0600` where the filesystem permits it. SQLite WAL mode allows several terminal and tmux sessions to write concurrently.

## Current-shell execution and command display

`tmem` consists of two pieces:

```text
Bash function
    resolves selections through tmem-core
    replaces the tmem invocation in Bash history
    redraws the previous prompt with the resolved command
    evaluates the command inside the calling shell

Python core
    stores/query history in SQLite
    runs the fzf interaction
    manages memories, groups, templates, and parameter values
```

The redraw uses normal ANSI terminal control sequences and accounts for ordinary wrapped invocation lines. Highly customized multiline prompts may not redraw perfectly. Raw terminal capture, shell auditing, or a multiplexer log may still observe that `tmem` was initially typed even though the visible terminal line and Bash history are replaced.

## Privacy and security

Terminal history can contain credentials, access tokens, database URLs, and sensitive arguments. Pause recording for the current shell with:

```bash
tmem pause
# sensitive work
tmem resume
```

Delete individual rows through the history detail menu.

Optional recording exclusions can be configured in:

```text
~/.config/tmem/config.json
```

Example:

```json
{
  "history_limit": 50000,
  "ignore_patterns": [
    "^\\s*tmem(?:\\s|$)",
    "^\\s*tmem-core(?:\\s|$)",
    "Authorization: Bearer",
    "postgres(?:ql)?://[^ ]+:[^ ]+@"
  ]
}
```

Patterns are Python regular expressions. `tmem` and `tmem-core` invocations are ignored by default. `history_limit` controls how many recent records are loaded into the interactive `fzf` view; it does not delete older database rows.
Invalid JSON, field types, limits, or regular expressions are reported instead of being silently ignored.

Runtime locations can be changed with:

| Variable | Purpose |
| --- | --- |
| `TMEM_DATA_DIR` | Directory containing the default database |
| `TMEM_CONFIG_DIR` | Directory containing `config.json` |
| `TMEM_DB` | Exact database path; takes precedence over `TMEM_DATA_DIR` |

The installer also accepts `TMEM_INSTALL_APP_DIR`, `TMEM_INSTALL_BIN_DIR`,
`TMEM_INSTALL_CONFIG_DIR`, and `TMEM_INSTALL_BASHRC` for custom destinations.

Saved memories and history entries are executable local data. Run only commands
you recognize, and protect the database from other users who could read or
modify it. Ignore patterns reduce accidental recording but are not a secret
scanner. See [SECURITY.md](SECURITY.md) for the trust model and vulnerability
reporting process.

## Development

Create an isolated development environment and install the test tools:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
make test
make build
```

The test suite includes a pseudo-terminal integration test proving that a resolved `cd /tmp`:

- changes the directory of the actual calling Bash process;
- is redrawn as the visible command;
- is recorded as `cd /tmp` rather than `tmem run …`.

See [CONTRIBUTING.md](CONTRIBUTING.md) for architecture constraints and pull
request guidance.

## Uninstall

```bash
./uninstall.sh
```

The uninstaller deliberately leaves `~/.local/share/tmem/tmem.db` in place. Delete that file manually only when the stored history should also be erased.
