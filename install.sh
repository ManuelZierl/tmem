#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
APP_DIR=${TMEM_INSTALL_APP_DIR:-"$HOME/.local/share/tmem/app"}
BIN_DIR=${TMEM_INSTALL_BIN_DIR:-"$HOME/.local/bin"}
CONFIG_DIR=${TMEM_INSTALL_CONFIG_DIR:-"$HOME/.config/tmem"}
BASHRC=${TMEM_INSTALL_BASHRC:-"$HOME/.bashrc"}
SOURCE_STATE=$CONFIG_DIR/bashrc-source-line
APP_MARKER=$APP_DIR/.tmem-install
FILE_MARKER='# tmem managed file'

if ! command -v python3 >/dev/null 2>&1; then
    printf 'tmem requires Python 3.10 or newer.\n' >&2
    exit 1
fi

if ! python3 - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
then
    printf 'tmem requires Python 3.10 or newer.\n' >&2
    exit 1
fi

legacy_install=0
if [[ -f $APP_DIR/tmem/__main__.py && -f $APP_DIR/tmem/cli.py &&
      -f $BIN_DIR/tmem-core ]] && grep -Fq 'python3 -m tmem' "$BIN_DIR/tmem-core"; then
    legacy_install=1
fi

managed_install=0
if [[ -f $APP_MARKER ]] && grep -Fqx 'tmem managed installation' "$APP_MARKER"; then
    managed_install=1
elif (( legacy_install )); then
    managed_install=1
fi

path_exists() {
    [[ -e $1 || -L $1 ]]
}

managed_file() {
    local path=$1 legacy_text=$2
    [[ -f $path ]] || return 1
    grep -Fqx "$FILE_MARKER" "$path" 2>/dev/null && return 0
    (( legacy_install )) && grep -Fq "$legacy_text" "$path" 2>/dev/null
}

refuse_unknown_target() {
    local path=$1 owned=$2
    if path_exists "$path" && (( ! owned )); then
        printf 'Refusing to overwrite non-tmem path: %s\n' "$path" >&2
        exit 1
    fi
}

app_owned=$managed_install
core_owned=0
launcher_owned=0
integration_owned=0
managed_file "$BIN_DIR/tmem-core" 'python3 -m tmem' && core_owned=1
managed_file "$BIN_DIR/tmem" 'current-shell execution requires the Bash integration' && launcher_owned=1
managed_file "$CONFIG_DIR/tmem.bash" '# tmem Bash integration' && integration_owned=1

refuse_unknown_target "$APP_DIR/tmem" "$app_owned"
refuse_unknown_target "$APP_MARKER" "$managed_install"
refuse_unknown_target "$BIN_DIR/tmem-core" "$core_owned"
refuse_unknown_target "$BIN_DIR/tmem" "$launcher_owned"
refuse_unknown_target "$CONFIG_DIR/tmem.bash" "$integration_owned"
if path_exists "$SOURCE_STATE" && ! grep -Fqx 'tmem managed bashrc source' "$SOURCE_STATE" 2>/dev/null; then
    printf 'Refusing to overwrite non-tmem path: %s\n' "$SOURCE_STATE" >&2
    exit 1
fi

previous_install=$managed_install
config_dir_existed=0
[[ -d $CONFIG_DIR ]] && config_dir_existed=1

mkdir -p "$APP_DIR" "$BIN_DIR" "$CONFIG_DIR"
if (( ! config_dir_existed )); then
    chmod 700 "$CONFIG_DIR" 2>/dev/null || true
fi
rm -rf "$APP_DIR/tmem"
cp -R "$ROOT_DIR/src/tmem" "$APP_DIR/tmem"
printf 'tmem managed installation\n' > "$APP_MARKER"
{
    printf '%s\n' "$FILE_MARKER"
    printf 'if [[ -z ${TMEM_CORE:-} ]]; then TMEM_CORE=%q; fi\n' "$BIN_DIR/tmem-core"
    cat "$ROOT_DIR/shell/tmem.bash"
} > "$CONFIG_DIR/tmem.bash"
chmod 600 "$CONFIG_DIR/tmem.bash"
if [[ ! -f $CONFIG_DIR/config.json ]]; then
    cat > "$CONFIG_DIR/config.json" <<'CONFIG'
{
  "history_limit": 50000,
  "ignore_patterns": [
    "^\\s*tmem(?:\\s|$)",
    "^\\s*tmem-core(?:\\s|$)"
  ]
}
CONFIG
    chmod 600 "$CONFIG_DIR/config.json"
fi

{
    printf '#!/usr/bin/env bash\n'
    printf '%s\n' "$FILE_MARKER"
    printf 'APP_DIR=%q\n' "$APP_DIR"
    printf 'CONFIG_DIR=%q\n' "$CONFIG_DIR"
    cat <<'LAUNCHER'
exec env PYTHONPATH="$APP_DIR" TMEM_CONFIG_DIR="${TMEM_CONFIG_DIR:-$CONFIG_DIR}" python3 -m tmem "$@"
LAUNCHER
} > "$BIN_DIR/tmem-core"
chmod 755 "$BIN_DIR/tmem-core"

# A fallback executable is useful before Bash integration is loaded and for
# non-executing commands in scripts. The Bash function shadows it interactively.
{
    printf '#!/usr/bin/env bash\n'
    printf '%s\n' "$FILE_MARKER"
    printf 'CORE=%q\n' "$BIN_DIR/tmem-core"
    printf 'INTEGRATION=%q\n' "$CONFIG_DIR/tmem.bash"
    cat <<'LAUNCHER'
case "${1-}" in
    ""|run)
        printf 'tmem: current-shell execution requires the Bash integration.\n' >&2
        printf 'Load it with: source %q\n' "$INTEGRATION" >&2
        exit 2
        ;;
    *)
        if "$CORE" memory-exists "$1" >/dev/null 2>&1; then
            printf 'tmem: current-shell execution requires the Bash integration.\n' >&2
            printf 'Load it with: source %q\n' "$INTEGRATION" >&2
            exit 2
        fi
        exec "$CORE" "$@"
        ;;
esac
LAUNCHER
} > "$BIN_DIR/tmem"
chmod 755 "$BIN_DIR/tmem"

if [[ $CONFIG_DIR == "$HOME/.config/tmem" ]]; then
    SOURCE_LINE='[[ -f "$HOME/.config/tmem/tmem.bash" ]] && source "$HOME/.config/tmem/tmem.bash"'
else
    printf -v integration_path '%q' "$CONFIG_DIR/tmem.bash"
    SOURCE_LINE="[[ -f $integration_path ]] && source $integration_path"
fi
if [[ ! -f $BASHRC ]]; then
    touch "$BASHRC"
fi
if ! grep -Fqx "$SOURCE_LINE" "$BASHRC"; then
    {
        printf '\n# tmem terminal command memory\n'
        printf '%s\n' "$SOURCE_LINE"
    } >> "$BASHRC"
    printf 'tmem managed bashrc source\ncomment=1\n%s\n' "$SOURCE_LINE" > "$SOURCE_STATE"
elif (( previous_install )) && [[ ! -f $SOURCE_STATE ]]; then
    # A previous tmem release added the same line but did not track ownership.
    printf 'tmem managed bashrc source\ncomment=0\n%s\n' "$SOURCE_LINE" > "$SOURCE_STATE"
fi
chmod 600 "$APP_MARKER"
[[ -f $SOURCE_STATE ]] && chmod 600 "$SOURCE_STATE"

printf 'Installed tmem.\n'
printf 'Load it in this shell with:\n\n'
printf '  source %q\n\n' "$CONFIG_DIR/tmem.bash"
if ! command -v fzf >/dev/null 2>&1; then
    printf 'The interactive UI also needs fzf:\n\n'
    printf '  sudo apt install fzf\n\n'
fi
if [[ :$PATH: != *":$BIN_DIR:"* ]]; then
    printf 'Add %s to PATH if your shell does not already do so.\n' "$BIN_DIR"
fi
