# Windows and macOS portability — draft implementation

**This branch is not ready to merge and does not yet provide working native Windows parity.**

The intended targets are Linux/Bash, macOS/zsh (with Bash retained), and Windows/PowerShell 7.3+. This is shell integration, not automatic translation of command syntax or installation of the programs used by saved commands.

## Design

The shared Python/SQLite/fzf application resolves a selection into an execution protocol. A shell adapter performs execution in the existing shell, so directory, environment, variable and function changes can survive. PowerShell uses PSReadLine to replace a standalone `tmem` invocation with the selected script before normal prompt-scope execution. Noninteractive PowerShell callers use explicit `. tmem run <memory>` dot-sourcing. Existing custom Enter bindings are preserved; that mode also uses explicit execution rather than silently overriding a user binding.

Memories retain their originating shell. Existing databases migrate to schema 3 with old memories tagged Bash, retaining IDs, steps, parameters and usage data. PowerShell and Unix-shell commands cannot execute across their language boundary. Bash and zsh share literal quoting, but shell-specific constructs are not translated and still need the original shell.

Parameter values receive shell-specific literal quoting, including PowerShell's Unicode quote delimiters. Placeholders inside quotes or escapes are rejected; write placeholders as whole unquoted argument tokens. The token picker is conservative, not a complete PowerShell parser.

## Implemented in this draft

- zsh preexec/precmd/history hooks, execution adapter and pause/resume.
- PowerShell adapter, strict execution-response validation and caller-scope strategy.
- Shell-aware quoting, grouped execution, memory metadata and selection guards.
- Windows data/config locations, clipboard/editor selection, zsh and PSReadLine history import.
- `tmem-core init bash|zsh|powershell`; shell resources packaged from one canonical source.
- Unix installers select Bash or zsh, respect custom paths and support both adapters side by side. Windows install/uninstall scripts use per-user locations and do not change execution policy or require administrator access. Uninstall preserves history and configuration.
- Regression tests for quoting, language boundaries, history import, migration, installer coexistence and native shell scope/status. CI covers Ubuntu, macOS and Windows with Python 3.10, 3.12 and 3.14.

The planned installer entry points are `./install.sh --shell zsh` on macOS and `./install.ps1` in PowerShell on Windows. **The presence of these installers is not a support claim: the runtime blockers below still apply.** The existing README describes the original Bash application, not a completed cross-platform release.

## Unresolved runtime and validation blockers

Publication of changes to `src/tmem/terminal_ui.py` and `shell/tmem.bash` was blocked by an OpenAI connector safety check. Those two files remain at their original versions. No alternate route was used to publish the blocked contents.

1. The terminal module still imports Unix `readline` and assumes `/dev/tty`. Native Windows needs a compatible console input/output implementation with Unicode handling and strict separation between terminal UI and the stdout execution protocol. This currently prevents Windows startup.
2. macOS/BSD date handling and Bash 3.2 multiline history still need implementation in the published Bash adapter. The BSD clock regression remains enabled and is expected to expose the missing behavior.
3. The final published head needs passing native tests. Local working-copy results that included unpublished changes are not validation of this branch. Native shell or installer failures discovered by CI must also be fixed before support is claimed.
4. Interactive acceptance still needs real Windows/macOS terminals: fzf selection/cancel, parameter input, Unicode, editor/clipboard, current-shell cwd/variables/functions, real history, exit status, and coexistence with user prompt/key hooks. POSIX pexpect tests are not Windows console tests; Windows noninteractive smoke tests alone do not cover the interactive PSReadLine path.

Windows PowerShell 5.1, cmd.exe, PowerShell ISE, and command-language translation are outside the target scope. zsh prints the resolved command on a new line rather than guessing the dimensions of a custom prompt for in-place redraw.
