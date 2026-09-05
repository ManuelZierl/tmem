# tmem

`tmem` is persistent, fuzzy-searchable terminal command memory for Bash, zsh, and PowerShell.

> **Portability:** Linux/Bash, macOS/zsh (plus Bash), and Windows/PowerShell 7.3+ use different shell integrations and have a few intentional semantic differences. Read [PORTABILITY.md](PORTABILITY.md) for the exact support contract, PowerShell fallback behavior, and cross-platform memory import/export guidance.

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
- multiple historical commands can be saved and run as an ordered command group;
- saved memory definitions can be exported/imported as versioned JSON for backup or transfer between machines.

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

The visible command and shell history contain the resolved command—not `tmem`—and it executes in the calling shell.

## Requirements

`tmem` targets interactive Bash on Linux, zsh on macOS (with Bash also supported), and PowerShell 7.3+ with PSReadLine on Windows. See [PORTABILITY.md](PORTABILITY.md) for the platform-specific behavior and limitations.

- Python 3.10 or newer;
- [`fzf`](https://github.com/junegunn/fzf) for the interactive interface;
- Bash/zsh standard command-line utilities on Unix, including `base64`, or PowerShell 7.3+ with PSReadLine on Windows.

The Python runtime uses only the standard library. `tmem` does not require a
server or network account.

## Quick start

Install dependencies on Ubuntu or Debian:

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

On macOS, select zsh explicitly when installing. On Windows, use the PowerShell installer. Exact platform notes and acceptance status live in [PORTABILITY.md](PORTABILITY.md).

The Unix installer:

- copies the Python application to `~/.local/share/tmem/app`;
- installs `tmem` and `tmem-core` under `~/.local/bin`;
- installs the selected Bash/zsh integration under the tmem config directory;
- adds one guarded source line to the corresponding shell profile.

Installed files carry ownership markers. The installer refuses to overwrite an unrelated file at one of its target paths, and the uninstaller removes only files and shell configuration recorded as tmem-owned.

The supported interactive installation paths are the platform installers. Installing the Python package alone provides `tmem-core` but does not install the required shell integration.

The interactive shell integration is essential. A standalone child process cannot change its parent shell, so `cd`, `source`, environment changes, functions and similar commands require the shell adapter. The adapters resolve the selected memory and arrange for the resulting command to execute in the current shell; the exact mechanics differ between Bash, zsh and PowerShell.

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

### Import and export

Use the versioned JSON interchange format to back up or move saved memories between machines:

```bash
tmem export > tmem-memories.json
tmem export deploy logs > team-memories.json
tmem import team-memories.json
```

Exported definitions retain each memory's shell, scope, description, group steps, stop-on-error setting and parameter defaults. History, run counts and remembered parameter values are intentionally not exported.

Directory scopes can be remapped while importing:

```bash
tmem import team-memories.json --scope preserve
tmem import team-memories.json --scope global
tmem import team-memories.json --scope here
```

Conflicts fail by default. Use `--on-conflict skip` or `--on-conflict replace` explicitly when that is desired. See [PORTABILITY.md](PORTABILITY.md#memory-import-and-export) for the rationale and cross-platform guidance.

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

In the parameter choice screen, type to filter saved values. If the text does
not match a saved value, pressing Enter uses the typed text as a new value and
remembers it. During parameter creation, press `Tab` on every token to mark all
parameters, then press Enter to continue.

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

```bash
tmem group activate -- 'cd /tmp/project' ::: 'source .venv/bin/activate'
tmem run activate
```

## Non-interactive history queries

```bash
tmem search docker
tmem failed deploy
tmem today
tmem cwd
tmem stats
```

Add `--json` to history query commands when machine-readable output is useful.

## History import

`tmem import-history` imports existing Bash, zsh or PSReadLine history into tmem's history database. This is separate from `tmem import`, which imports saved **memory definitions**.

```bash
tmem import-history ~/.bash_history --shell bash
tmem import-history ~/.zsh_history --shell zsh
```

The PowerShell integration exposes the current PSReadLine history path, so `tmem import-history --shell powershell` can use it automatically when the integration is loaded.

History imports are idempotent: tmem records source identities and skips entries already imported.

## Privacy and security

`tmem` stores command history locally in SQLite. Commands may contain credentials, tokens, personal data or other secrets. Treat the tmem database and exported history data accordingly.

Saved-memory exports are narrower than a database backup: they contain reusable command templates and parameter **defaults**, but not command history or remembered parameter values. Defaults can still contain sensitive values, so inspect a memory export before sharing it.

`tmem` does not upload history to a service and does not require a network account.

Execution remains shell execution. Selecting a historical command or saved memory runs it with the permissions of the current shell. Review unfamiliar commands before running them, particularly imported memories.
