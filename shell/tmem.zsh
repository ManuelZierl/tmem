# tmem zsh integration. Source from ~/.zshrc (or use `tmem-core init zsh`).
[[ -n ${_TMEM_ZSH_LOADED:-} ]] && return 0
typeset -g _TMEM_ZSH_LOADED=1
export TMEM_SHELL=zsh TMEM_SHELL_INTEGRATION=1
export TMEM_SESSION_ID="${HOST:-host}:$$:$(date +%s):${RANDOM}"

_tmem_core() {
    if [[ -n ${TMEM_CORE:-} ]]; then
        "$TMEM_CORE" "$@"
    elif (( $+commands[tmem-core] )); then
        command tmem-core "$@"
    elif [[ -x $HOME/.local/bin/tmem-core ]]; then
        "$HOME/.local/bin/tmem-core" "$@"
    else
        print -u2 -- 'tmem-core was not found. Re-run install.sh or fix PATH.'
        return 127
    fi
}

_tmem_now_ms() {
    if zmodload zsh/datetime 2>/dev/null; then
        printf '%.0f\n' "$(( EPOCHREALTIME * 1000 ))"
    else
        print -r -- "$(date +%s)000"
    fi
}

_tmem_zsh_preexec() {
    typeset -g _TMEM_PENDING_COMMAND=$1 _TMEM_PENDING_CWD=$PWD
    typeset -g _TMEM_PENDING_STARTED_MS=$(_tmem_now_ms)
    return 0
}

_tmem_zsh_precmd() {
    local command_status=$?
    if [[ -n ${_TMEM_PENDING_COMMAND:-} && ${TMEM_PAUSED:-0} != 1 ]]; then
        print -rn -- "$_TMEM_PENDING_COMMAND" | _tmem_core record \
            --cwd "$_TMEM_PENDING_CWD" --exit-code "$command_status" \
            --started-at-ms "$_TMEM_PENDING_STARTED_MS" \
            --hostname "${HOST:-}" --session "$TMEM_SESSION_ID" --shell zsh \
            >/dev/null 2>&1 || true
    fi
    unset _TMEM_PENDING_COMMAND _TMEM_PENDING_CWD _TMEM_PENDING_STARTED_MS
    return 0
}

_tmem_zsh_history() {
    # The resolved command is inserted instead; do not keep the tmem invocation.
    [[ $1 == tmem || $1 == tmem\ * || $1 == $'tmem\n' ]] && return 1
    return 0
}

_tmem_execute_payload() {
    local payload=$1
    [[ -n $payload ]] || return 0
    local action script_b64 display_b64 memory_id extra script display
    IFS=$'\t' read -r action script_b64 display_b64 memory_id extra <<< "$payload"
    if [[ $payload == *$'\n'* || $action != execute || -z $script_b64 || -z $display_b64 || -n $extra ]]; then
        print -u2 -- 'tmem: invalid execution response'
        return 2
    fi
    script=$(print -rn -- "$script_b64" | base64 -d) || return 2
    display=$(print -rn -- "$display_b64" | base64 -d) || return 2
    [[ -n $script && -n $display ]] || return 2
    if [[ -o interactive ]]; then
        print -s -- "$display"
        [[ -n ${HISTFILE:-} ]] && fc -AI 2>/dev/null
    fi
    typeset -g _TMEM_PENDING_COMMAND=$display
    typeset -g _TMEM_PENDING_CWD=${_TMEM_PENDING_CWD:-$PWD}
    typeset -g _TMEM_PENDING_STARTED_MS=${_TMEM_PENDING_STARTED_MS:-$(_tmem_now_ms)}
    # Avoid guessing the dimensions of a multiline/custom prompt. Always show
    # the real command; Up/Down history also receives the real command above.
    print -r -- "$display"
    eval -- "$script"
    local command_status=$?
    if [[ $memory_id == <-> ]]; then
        _tmem_core note-run "$memory_id" >/dev/null 2>&1 || true
    fi
    return "$command_status"
}

_tmem_resolve_and_execute() {
    local payload
    payload=$(_tmem_core "$@") || return $?
    _tmem_execute_payload "$payload"
}

tmem() {
    case ${1:-} in
        '') _tmem_resolve_and_execute shell-ui ;;
        run)
            shift
            if (( $# == 0 )); then
                print -u2 -- 'Usage: tmem run <memory> [parameter ...]'
                return 2
            fi
            _tmem_resolve_and_execute shell-run "$@"
            ;;
        pause) export TMEM_PAUSED=1; print -- 'tmem recording paused for this shell.' ;;
        resume) unset TMEM_PAUSED; print -- 'tmem recording resumed for this shell.' ;;
        status)
            if [[ ${TMEM_PAUSED:-0} == 1 ]]; then
                print -- 'tmem recording is paused for this shell.'
            else
                print -- 'tmem recording is active for this shell.'
            fi
            ;;
        help) _tmem_core --help ;;
        search|failed|today|cwd|list|show|edit|rm|remove|save|group|stats|import-history|doctor|init|--help|-h|--version)
            _tmem_core "$@" ;;
        *)
            if _tmem_core memory-exists "$1" >/dev/null 2>&1; then
                _tmem_resolve_and_execute shell-run "$@"
            else
                _tmem_core "$@"
            fi
            ;;
    esac
}

if [[ -o interactive ]]; then
    autoload -Uz add-zsh-hook
    add-zsh-hook preexec _tmem_zsh_preexec
    add-zsh-hook precmd _tmem_zsh_precmd
    add-zsh-hook zshaddhistory _tmem_zsh_history
    export TMEM_CAPTURE_MODE=preexec
fi
