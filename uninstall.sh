#!/usr/bin/env bash
set -euo pipefail

BIN_DIR=${TMEM_INSTALL_BIN_DIR:-"$HOME/.local/bin"}
CONFIG_DIR=${TMEM_INSTALL_CONFIG_DIR:-"$HOME/.config/tmem"}
TARGET_SHELL=${TMEM_INSTALL_SHELL:-${SHELL:-bash}}
TARGET_SHELL=${TARGET_SHELL##*/}
if [[ -z ${TMEM_INSTALL_SHELL:-} && $TARGET_SHELL != zsh ]]; then TARGET_SHELL=bash; fi
if [[ ${1:-} == --shell && $# == 2 ]]; then
    TARGET_SHELL=$2
elif (( $# != 0 )); then
    printf 'Usage: %s [--shell bash|zsh]\n' "$0" >&2
    exit 2
fi
case "$TARGET_SHELL" in
    bash)
        DEFAULT_RC=$HOME/.bashrc
        [[ $(uname -s) == Darwin ]] && DEFAULT_RC=$HOME/.bash_profile
        RCFILE=${TMEM_INSTALL_BASHRC:-$DEFAULT_RC}
        ;;
    zsh) RCFILE=${TMEM_INSTALL_ZSHRC:-${ZDOTDIR:-$HOME}/.zshrc} ;;
    *) printf 'Supported shells: bash, zsh\n' >&2; exit 2 ;;
esac
APP_DIR=${TMEM_INSTALL_APP_DIR:-"$HOME/.local/share/tmem/app"}
SOURCE_STATE=$CONFIG_DIR/${TARGET_SHELL}rc-source-line
APP_MARKER=$APP_DIR/.tmem-install
FILE_MARKER='# tmem managed file'

legacy_install=0
if [[ -f $APP_DIR/tmem/__main__.py && -f $APP_DIR/tmem/cli.py &&
      -f $BIN_DIR/tmem-core ]] && grep -Fq 'python3 -m tmem' "$BIN_DIR/tmem-core"; then
    legacy_install=1
fi

managed_file() {
    local path=$1 legacy_text=$2
    [[ -f $path ]] || return 1
    grep -Fqx "$FILE_MARKER" "$path" 2>/dev/null && return 0
    (( legacy_install )) && grep -Fq "$legacy_text" "$path" 2>/dev/null
}

source_state_owned=0
if [[ -f $SOURCE_STATE ]] && grep -Fqx "tmem managed ${TARGET_SHELL}rc source" "$SOURCE_STATE"; then
    source_state_owned=1
fi
if (( source_state_owned )) && [[ -f $RCFILE ]]; then
    {
        IFS= read -r _
        IFS= read -r COMMENT_STATE
        IFS= read -r SOURCE_LINE
    } < "$SOURCE_STATE"
    if [[ -n $SOURCE_LINE ]]; then
        temporary=$(mktemp "$RCFILE.tmem.XXXXXX")
        pending=""
        pending_set=0
        removed=0
        while IFS= read -r line || [[ -n $line ]]; do
            if (( ! removed )) && [[ $line == "$SOURCE_LINE" ]]; then
                if (( pending_set )) &&
                   { [[ $COMMENT_STATE != comment=1 ]] || [[ $pending != '# tmem terminal command memory' ]]; }; then
                    printf '%s\n' "$pending" >> "$temporary"
                fi
                pending=""
                pending_set=0
                removed=1
                continue
            fi
            if (( pending_set )); then
                printf '%s\n' "$pending" >> "$temporary"
            fi
            pending=$line
            pending_set=1
        done < "$RCFILE"
        if (( pending_set )); then
            printf '%s\n' "$pending" >> "$temporary"
        fi
        chmod --reference="$RCFILE" "$temporary" 2>/dev/null || true
        mv "$temporary" "$RCFILE"
    fi
fi
managed_file "$CONFIG_DIR/tmem.$TARGET_SHELL" '# tmem Bash integration' && rm -f "$CONFIG_DIR/tmem.$TARGET_SHELL"
(( source_state_owned )) && rm -f "$SOURCE_STATE"

# Bash and zsh share the application. Removing one adapter must not break the
# other; remove the shared files only after the last managed adapter is gone.
other_shell=bash
[[ $TARGET_SHELL == bash ]] && other_shell=zsh
if ! managed_file "$CONFIG_DIR/tmem.$other_shell" '# tmem Bash integration'; then
    managed_file "$BIN_DIR/tmem" 'current-shell execution requires the Bash integration' && rm -f "$BIN_DIR/tmem"
    managed_file "$BIN_DIR/tmem-core" 'python3 -m tmem' && rm -f "$BIN_DIR/tmem-core"
    if { [[ -f $APP_MARKER ]] && grep -Fqx 'tmem managed installation' "$APP_MARKER"; } || (( legacy_install )); then
        rm -rf "$APP_DIR/tmem"
        rm -f "$APP_MARKER"
    fi
    rmdir "$APP_DIR" 2>/dev/null || true
fi

printf 'Removed the %s integration (shared application retained while another adapter is installed).\n' "$TARGET_SHELL"
printf 'Command history was not removed. Delete the database manually to erase the data.\n'
