# Auto-generated bash completion for `schema_docs_cli`.
# Source this file or drop it into /etc/bash_completion.d/.
# Regenerate via: python -m tools.completion_cli bash ...

_schema_docs_cli_complete() {
    local cur prev words cword
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    # Build a space-joined trail of the subcommand path we've
    # already typed (skipping options + the program name itself).
    local trail=""
    local i
    for (( i=1; i<COMP_CWORD; i++ )); do
        local w="${COMP_WORDS[i]}"
        # Skip flags and their following value (best-effort —
        # we don't know which flags take values, so we treat
        # ``--foo=bar`` and ``--foo bar`` the same: just drop
        # both tokens). This keeps trail-matching robust for the
        # common operator pattern ``ops_cli alerts list --json``.
        if [[ "$w" == -* ]]; then
            continue
        fi
        if [[ -z "$trail" ]]; then
            trail="$w"
        else
            trail="$trail $w"
        fi
    done

    local suggestions=""
    case "$trail" in
        "")
            suggestions="history json markdown"
            ;;
        "history")
            suggestions=""
            ;;
        "json")
            suggestions=""
            ;;
        "markdown")
            suggestions=""
            ;;
        *)
            suggestions=""
            ;;
    esac

    # If the current token starts with '-', also fall back to
    # the parser's known long/short options. We don't try to
    # scope these per-subcommand — operators rarely ask for
    # option-name completion deep in the tree, and any miss is
    # only a missing suggestion (never an incorrect one).
    if [[ "$cur" == -* ]]; then
        suggestions="$suggestions --help -h"
    fi

    COMPREPLY=( $(compgen -W "$suggestions" -- "$cur") )
    return 0
}

complete -F _schema_docs_cli_complete schema_docs_cli
