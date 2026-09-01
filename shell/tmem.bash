# tmem Bash integration
# shellcheck shell=bash

if [[ -n ${_TMEM_BASH_LOADED:-} ]]; then
    return 0 2>/dev/null || exit 0
fi
_TMEM_BASH_LOADED=1
export TMEM_SHELL_INTEGRATION=1

: "${TMEM_SESSION_ID:=${HOSTNAME:-host}:$$:$(date +%s):${RANDOM}}"
export TMEM_SESSION_ID

_TMEM_READY=0
_TMEM_IN_PROMPT=0
_TMEM_IN_DEBUG=0
_TMEM_LAST_HISTCMD=${HISTCMD:-0}

_tmem_core() {
    if [[ -n ${TMEM_CORE:-} ]]; then
        "$TMEM_CORE" "$@"
    elif command -v tmem-core >/dev/null 2>&1; then
        command tmem-core "$@"
    elif [[ -x $HOME/.local/bin/tmem-core ]]; then
        "$HOME/.local/bin/tmem-core" "$@"
    else
        printf 'tmem-core was not found. Re-run install.sh or add ~/.local/bin to PATH.\n' >&2
        return 127
    fi
}

_tmem_trim_history_command() {
    local value=$1
    # `fc -ln` adds indentation for display. Remove that indentation while
    # retaining the command's internal formatting.
    value="${value#"${value%%[!$' \t']*}"}"
    printf '%s' "$value"
}

_tmem_preexec_capture() {
    local bash_command=${1-}
    # Bash invokes the DEBUG trap immediately before the first PROMPT_COMMAND
    # function as well. That is prompt machinery, not a user-entered command.
    [[ $bash_command == _tmem_precmd_start* ]] && return 0
    [[ ${_TMEM_READY:-0} == 1 ]] || return 0
    [[ ${_TMEM_IN_PROMPT:-0} == 0 ]] || return 0
    [[ ${_TMEM_INTERNAL:-0} == 0 ]] || return 0

    _TMEM_READY=0
    _TMEM_PENDING_CWD=$PWD
    _TMEM_PENDING_STARTED_MS=$(date +%s%3N 2>/dev/null || date +%s000)

    local current_histcmd=${HISTCMD:-0}
    local history_command=""
    if (( current_histcmd > ${_TMEM_LAST_HISTCMD:-0} )); then
        history_command=$(builtin fc -ln -1 2>/dev/null) || history_command=""
        history_command=$(_tmem_trim_history_command "$history_command")
    fi

    # If Bash did not add this line to its own history (for example because of
    # HISTCONTROL=ignorespace/ignoredups), BASH_COMMAND is the best available
    # representation. For normal lines, `fc` preserves the complete command,
    # including pipelines and && chains.
    if [[ -n $history_command ]]; then
        _TMEM_PENDING_COMMAND=$history_command
    else
        _TMEM_PENDING_COMMAND=$bash_command
    fi
}

_tmem_debug_dispatch() {
    local captured_command=${1-}
    local prior_status=${2:-0}
    if [[ ${_TMEM_IN_DEBUG:-0} == 1 ]]; then
        return "$prior_status"
    fi
    _TMEM_IN_DEBUG=1

    _tmem_preexec_capture "$captured_command"

    _TMEM_IN_DEBUG=0
    return "$prior_status"
}

_tmem_precmd_start() {
    local command_status=$?
    _TMEM_IN_PROMPT=1
    _TMEM_READY=0

    # At prompt time Bash has finalized multiline history entries. Refresh the
    # preexec value here so continuations are stored as one complete command.
    if [[ ${HISTCMD:-0} -gt ${_TMEM_LAST_HISTCMD:-0} &&
          ( -n ${_TMEM_PENDING_COMMAND:-} || ${TMEM_CAPTURE_MODE:-} == prompt-fallback ) ]]; then
        local fallback_command
        fallback_command=$(builtin fc -ln -1 2>/dev/null) || fallback_command=""
        fallback_command=$(_tmem_trim_history_command "$fallback_command")
        if [[ -n $fallback_command ]]; then
            if [[ -z ${_TMEM_PENDING_COMMAND:-} ||
                  $fallback_command == "$_TMEM_PENDING_COMMAND"* ]]; then
                _TMEM_PENDING_COMMAND=$fallback_command
                _TMEM_PENDING_CWD=${_TMEM_PENDING_CWD:-$PWD}
            fi
        fi
    fi

    if [[ -n ${_TMEM_PENDING_COMMAND:-} && ${TMEM_PAUSED:-0} != 1 ]]; then
        local -a record_args=(
            record
            --cwd "${_TMEM_PENDING_CWD:-$PWD}"
            --exit-code "$command_status"
            --hostname "${HOSTNAME:-}"
            --session "$TMEM_SESSION_ID"
            --shell bash
        )
        if [[ -n ${_TMEM_PENDING_STARTED_MS:-} ]]; then
            record_args+=(--started-at-ms "$_TMEM_PENDING_STARTED_MS")
        fi
        printf '%s' "$_TMEM_PENDING_COMMAND" |
            TMEM_INTERNAL=1 _tmem_core "${record_args[@]}" >/dev/null 2>&1 || true
    fi

    unset _TMEM_PENDING_COMMAND _TMEM_PENDING_CWD _TMEM_PENDING_STARTED_MS
    return "$command_status"
}

_tmem_precmd_end() {
    local prompt_status=$?
    _TMEM_LAST_HISTCMD=${HISTCMD:-$_TMEM_LAST_HISTCMD}
    _TMEM_IN_PROMPT=0
    _TMEM_READY=1
    return "$prompt_status"
}

_tmem_install_prompt_hooks() {
    local declaration
    declaration=$(declare -p PROMPT_COMMAND 2>/dev/null || true)
    if [[ $declaration == "declare -a"* ]]; then
        local -a previous=("${PROMPT_COMMAND[@]}")
        PROMPT_COMMAND=(_tmem_precmd_start "${previous[@]}" _tmem_precmd_end)
    else
        local previous=${PROMPT_COMMAND-}
        PROMPT_COMMAND="_tmem_precmd_start;${previous:+$previous;}_tmem_precmd_end"
    fi
}

_tmem_install_debug_trap() {
    local existing
    existing=$(trap -p DEBUG)
    if [[ -n $existing && $existing != *"_tmem_debug_dispatch"* ]]; then
        # Do not replace an unrelated DEBUG trap. The prompt hook still records
        # normal Bash-history entries, while deliberately avoiding interference
        # with debuggers or another preexec framework.
        TMEM_CAPTURE_MODE=prompt-fallback
        export TMEM_CAPTURE_MODE
        return 0
    fi
    builtin trap '_tmem_debug_dispatch "$BASH_COMMAND" "$?"' DEBUG
    TMEM_CAPTURE_MODE=preexec
    export TMEM_CAPTURE_MODE
}

_tmem_remove_invocation_from_history() {
    local last
    last=$(builtin fc -ln -1 2>/dev/null) || return 0
    last=$(_tmem_trim_history_command "$last")
    if [[ $last == tmem || $last == tmem\ * ]]; then
        if ! builtin history -d -1 2>/dev/null; then
            local listing number rest
            listing=$(HISTTIMEFORMAT= builtin history 1 2>/dev/null) || return 0
            read -r number rest <<<"$listing"
            [[ $number =~ ^[0-9]+$ ]] && builtin history -d "$number" 2>/dev/null || true
        fi
    fi
}

_tmem_plain_prompt_length() {
    local prompt=$1
    # Remove readline's non-printing markers and common ANSI CSI sequences.
    prompt=${prompt//$'\001'/}
    prompt=${prompt//$'\002'/}
    prompt=$(printf '%s' "$prompt" | sed $'s/\033\\[[0-9;?]*[ -\\/]*[@-~]//g' 2>/dev/null)
    printf '%s' "${#prompt}"
}

_tmem_redraw_real_command() {
    local display=$1
    local invocation=${2:-tmem}
    if [[ ! -t 1 || ${TERM:-dumb} == dumb || ${TMEM_REDRAW:-1} == 0 ]]; then
        printf '%s\n' "$display"
        return 0
    fi

    local prompt
    if (( BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 4) )); then
        prompt=${PS1@P}
    else
        prompt=$PS1
    fi
    prompt=${prompt//$'\001'/}
    prompt=${prompt//$'\002'/}

    local columns=${COLUMNS:-80}
    [[ $columns =~ ^[0-9]+$ ]] || columns=80
    local prompt_length
    prompt_length=$(_tmem_plain_prompt_length "$prompt")
    local invocation_length=${#invocation}
    local rows=$(( (prompt_length + invocation_length) / columns + 1 ))
    (( rows < 1 )) && rows=1

    local index
    for ((index = 0; index < rows; index++)); do
        printf '\033[1A\r\033[2K'
    done

    local continuation
    if (( BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 4) )); then
        continuation=${PS2@P}
    else
        continuation=${PS2:-'> '}
    fi
    continuation=${continuation//$'\001'/}
    continuation=${continuation//$'\002'/}

    local first=1 line
    while IFS= read -r line || [[ -n $line ]]; do
        if (( first )); then
            printf '%s%s\n' "$prompt" "$line"
            first=0
        else
            printf '%s%s\n' "$continuation" "$line"
        fi
    done <<<"$display"
}

_tmem_execute_payload() {
    local payload=$1
    [[ -n $payload ]] || return 0

    local action script_b64 display_b64 memory_id extra
    IFS=$'\t' read -r action script_b64 display_b64 memory_id extra <<<"$payload"
    if [[ $payload == *$'\n'* || $action != execute || -z $script_b64 || -z $display_b64 || -n $extra ]]; then
        printf 'tmem: invalid execution response\n' >&2
        return 2
    fi

    local script display invocation
    script=$(printf '%s' "$script_b64" | base64 --decode) || return 2
    display=$(printf '%s' "$display_b64" | base64 --decode) || return 2
    invocation=${_TMEM_PENDING_COMMAND:-tmem}

    _tmem_remove_invocation_from_history
    builtin history -s "$display" 2>/dev/null || true
    builtin history -a 2>/dev/null || true

    # The prompt hook records the real resolved command and its eventual status,
    # rather than the tmem invocation that led to it.
    _TMEM_PENDING_CWD=${_TMEM_PENDING_CWD:-$PWD}
    _TMEM_PENDING_STARTED_MS=${_TMEM_PENDING_STARTED_MS:-$(date +%s%3N 2>/dev/null || date +%s000)}
    _TMEM_PENDING_COMMAND=$display
    _tmem_redraw_real_command "$display" "$invocation"

    builtin eval -- "$script"
    local command_status=$?
    if [[ $memory_id =~ ^[0-9]+$ ]]; then
        TMEM_INTERNAL=1 _tmem_core note-run "$memory_id" >/dev/null 2>&1 || true
    fi
    return "$command_status"
}

_tmem_resolve_and_execute() {
    local payload
    payload=$(TMEM_INTERNAL=1 _tmem_core "$@")
    local core_status=$?
    (( core_status == 0 )) || return "$core_status"
    _tmem_execute_payload "$payload"
}

tmem() {
    local subcommand=${1-}
    case "$subcommand" in
        "")
            _tmem_resolve_and_execute shell-ui
            ;;
        run)
            shift
            if (($# == 0)); then
                printf 'Usage: tmem run <memory> [parameter ...]\n' >&2
                return 2
            fi
            _tmem_resolve_and_execute shell-run "$@"
            ;;
        pause)
            TMEM_PAUSED=1
            export TMEM_PAUSED
            printf 'tmem recording paused for this shell.\n'
            ;;
        resume)
            unset TMEM_PAUSED
            printf 'tmem recording resumed for this shell.\n'
            ;;
        status)
            if [[ ${TMEM_PAUSED:-0} == 1 ]]; then
                printf 'tmem recording is paused for this shell.\n'
            else
                printf 'tmem recording is active for this shell.\n'
            fi
            ;;
        help)
            _tmem_core --help
            ;;
        search|failed|today|cwd|list|show|edit|rm|remove|save|group|stats|import-history|doctor|--help|-h|--version)
            _tmem_core "$@"
            ;;
        *)
            # Saved memories are directly runnable as `tmem <name>` as well as
            # through the explicit `tmem run <name>` form.
            if TMEM_INTERNAL=1 _tmem_core memory-exists "$subcommand" >/dev/null 2>&1; then
                _tmem_resolve_and_execute shell-run "$@"
            else
                _tmem_core "$@"
            fi
            ;;
    esac
}

if [[ $- == *i* ]]; then
    _tmem_install_prompt_hooks
    _tmem_install_debug_trap
fi
