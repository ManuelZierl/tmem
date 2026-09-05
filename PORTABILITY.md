# Cross-platform behavior

`tmem` targets three interactive shell environments:

- Linux with Bash;
- macOS with zsh, with Bash also supported;
- Windows with PowerShell 7.3+ and PSReadLine.

This is shell integration, not command-language translation. A Bash/zsh command is not automatically converted to PowerShell, and a PowerShell command is not converted to Bash/zsh. Memories therefore retain the shell in which they were created and tmem refuses to execute a memory across the PowerShell ↔ Unix-shell language boundary.

## Shared guarantees

Across the supported shells, tmem is designed to preserve the same core model:

- history is recorded with shell identity;
- saved memories and command groups execute in the current interactive shell rather than an isolated child shell;
- directory, environment and other shell-state changes can therefore survive execution;
- parameters are quoted according to the memory's command language;
- groups stop after the first failing step by default;
- the same SQLite data model and interactive tmem UI are used on every platform;
- exported memory files are platform-independent JSON definitions and retain each memory's originating shell.

Existing databases migrate to schema 3. Pre-portability memories are tagged as Bash while retaining their IDs, steps, parameters and usage information.

## Linux / Bash

Bash is the original integration and has the strongest interactive test coverage. It uses DEBUG/PROMPT_COMMAND hooks and Bash history to capture the complete command that actually ran.

GNU and BSD differences that matter to tmem are handled explicitly. In particular, timestamps do not require GNU `date +%N`, payload decoding works with GNU and BSD `base64`, and the adapter does not depend on modern Bash-only prompt expansion when running on macOS Bash 3.2.

## macOS / zsh

zsh uses native `preexec`/`precmd` hooks and zsh history semantics. Saved commands execute in the current zsh so changes such as `cd`, variable assignments, sourced scripts and functions can affect the calling shell.

The zsh adapter intentionally does not attempt to redraw an arbitrary custom prompt in place. The resolved command is printed on a new line before execution. This avoids corrupting multi-line or plugin-managed prompts.

macOS users who deliberately use Bash can install the Bash adapter instead; its Bash 3.2 compatibility paths are tested separately.

## Windows / PowerShell

Native Windows support targets PowerShell 7.3 or newer. Windows PowerShell 5.1, cmd.exe and PowerShell ISE are outside the support target.

PowerShell uses PSReadLine for the primary interactive execution path. When Enter is pressed on a standalone `tmem ...` command, tmem resolves the selected memory and replaces the line editor buffer with the actual PowerShell script. PowerShell then executes that script normally at prompt scope. This is what allows variables, functions, `Set-Location`, environment changes and native `$?` / `$LASTEXITCODE` behavior to remain PowerShell-native.

`tmem` preserves an existing custom Enter key binding rather than silently replacing it. In that case, or from scripts, explicit execution remains available:

```powershell
. tmem run <memory>
```

This fallback deliberately has one semantic difference. It preserves caller scope and `$LASTEXITCODE`, but PowerShell does not provide a transparent way for a normal function boundary to forward the automatic `$?` variable. Manufacturing a terminating error would make `$?` false but would change normal native-command control flow, so tmem does not do that. For normal interactive PSReadLine use this limitation does not apply because the resolved script is executed directly rather than through the tmem function.

PowerShell command groups use `$()` statement subexpressions as operands of `&&` chains. This keeps state changes in the current scope while preserving PowerShell 7's native success/failure semantics between group steps.

Windows terminal I/O is UTF-8 explicitly, rather than inheriting the legacy Windows ANSI code page. Editors inherit the PowerShell console's stdin/stderr while stdout remains reserved for tmem's shell execution protocol.

## Memory import and export

The SQLite database is an implementation detail and should not be copied between platforms as the normal way to share memories. Use the versioned interchange format instead:

```text
tmem export > tmem-memories.json
tmem export deploy logs > team-memories.json
tmem import team-memories.json
```

`tmem export` without names exports every saved memory. A named export resolves names in the same current-directory-first manner as normal memory execution.

The JSON format contains only the declarative memory definition:

- name and description;
- originating shell;
- global or directory scope;
- stop-on-error behavior;
- command/group steps;
- parameter defaults.

It intentionally does **not** export command history, run counts, timestamps, or remembered parameter values. Those represent local usage/activity rather than the reusable memory definition.

Directory scopes are absolute paths and therefore frequently machine-specific. Import provides explicit remapping:

```text
tmem import memories.json --scope preserve
tmem import memories.json --scope global
tmem import memories.json --scope here
```

`preserve` is the default and is appropriate for backups or machines with the same layout. `global` is usually best when sharing memories across operating systems. `here` binds every imported memory to the current directory on the receiving machine.

Conflicts fail closed by default. The caller must choose an alternative policy explicitly:

```text
tmem import memories.json --on-conflict error
tmem import memories.json --on-conflict skip
tmem import memories.json --on-conflict replace
```

The interchange document contains a format identifier and version number. Import rejects unknown versions rather than guessing, so the format can evolve without silently corrupting memory definitions.

## Installation and platform validation

Unix installation supports selecting Bash or zsh and can keep both adapters installed side by side. Windows installation is per-user and does not require administrator access or change PowerShell execution policy. Uninstall preserves user history/configuration and removes only tmem-owned integration files.

CI covers Ubuntu, macOS and Windows with Python 3.10, 3.12 and 3.14 plus package builds. Native CI is necessary but not equivalent to a real interactive terminal. Before a cross-platform release, acceptance should still exercise real Windows Terminal and macOS terminals for fzf selection/cancel, parameter prompts, Unicode, editor/clipboard behavior, history, current-shell state, failure status and coexistence with user prompt/key customizations.
