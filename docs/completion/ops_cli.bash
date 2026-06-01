# Auto-generated bash completion for `ops_cli`.
# Source this file or drop it into /etc/bash_completion.d/.
# Regenerate via: python -m tools.completion_cli bash ...

_ops_cli_complete() {
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
            suggestions="alerts annotations anomalies audit calendar channels digest escalations export filters health health-alerts incidents invite mfa perf perf-budgets prefs reports retries rules schedules settings silences status telemetry tokens users"
            ;;
        "alerts")
            suggestions="ack ack-all list metrics"
            ;;
        "alerts ack")
            suggestions=""
            ;;
        "alerts ack-all")
            suggestions=""
            ;;
        "alerts list")
            suggestions=""
            ;;
        "alerts metrics")
            suggestions=""
            ;;
        "annotations")
            suggestions="add delete list"
            ;;
        "annotations add")
            suggestions=""
            ;;
        "annotations delete")
            suggestions=""
            ;;
        "annotations list")
            suggestions=""
            ;;
        "anomalies")
            suggestions="check configs disable enable set"
            ;;
        "anomalies check")
            suggestions=""
            ;;
        "anomalies configs")
            suggestions=""
            ;;
        "anomalies disable")
            suggestions=""
            ;;
        "anomalies enable")
            suggestions=""
            ;;
        "anomalies set")
            suggestions=""
            ;;
        "audit")
            suggestions="export"
            ;;
        "audit export")
            suggestions=""
            ;;
        "calendar")
            suggestions="export token-generate token-revoke token-show"
            ;;
        "calendar export")
            suggestions=""
            ;;
        "calendar token-generate")
            suggestions=""
            ;;
        "calendar token-revoke")
            suggestions=""
            ;;
        "calendar token-show")
            suggestions=""
            ;;
        "channels")
            suggestions="delete list reset-usage set-budget usage"
            ;;
        "channels delete")
            suggestions=""
            ;;
        "channels list")
            suggestions=""
            ;;
        "channels reset-usage")
            suggestions=""
            ;;
        "channels set-budget")
            suggestions=""
            ;;
        "channels usage")
            suggestions=""
            ;;
        "digest")
            suggestions="config disable enable preview send-now"
            ;;
        "digest config")
            suggestions=""
            ;;
        "digest disable")
            suggestions=""
            ;;
        "digest enable")
            suggestions=""
            ;;
        "digest preview")
            suggestions=""
            ;;
        "digest send-now")
            suggestions=""
            ;;
        "escalations")
            suggestions="add clear delete list"
            ;;
        "escalations add")
            suggestions=""
            ;;
        "escalations clear")
            suggestions=""
            ;;
        "escalations delete")
            suggestions=""
            ;;
        "escalations list")
            suggestions=""
            ;;
        "export")
            suggestions=""
            ;;
        "filters")
            suggestions="delete list"
            ;;
        "filters delete")
            suggestions=""
            ;;
        "filters list")
            suggestions=""
            ;;
        "health")
            suggestions="ping summary"
            ;;
        "health ping")
            suggestions=""
            ;;
        "health summary")
            suggestions=""
            ;;
        "health-alerts")
            suggestions="disable enable run-once status"
            ;;
        "health-alerts disable")
            suggestions=""
            ;;
        "health-alerts enable")
            suggestions=""
            ;;
        "health-alerts run-once")
            suggestions=""
            ;;
        "health-alerts status")
            suggestions=""
            ;;
        "incidents")
            suggestions="list stats"
            ;;
        "incidents list")
            suggestions=""
            ;;
        "incidents stats")
            suggestions=""
            ;;
        "invite")
            suggestions="create list revoke"
            ;;
        "invite create")
            suggestions=""
            ;;
        "invite list")
            suggestions=""
            ;;
        "invite revoke")
            suggestions=""
            ;;
        "mfa")
            suggestions="disable enable recovery-codes regenerate-codes status"
            ;;
        "mfa disable")
            suggestions=""
            ;;
        "mfa enable")
            suggestions=""
            ;;
        "mfa recovery-codes")
            suggestions=""
            ;;
        "mfa regenerate-codes")
            suggestions=""
            ;;
        "mfa status")
            suggestions=""
            ;;
        "perf")
            suggestions="summary"
            ;;
        "perf summary")
            suggestions=""
            ;;
        "perf-budgets")
            suggestions="check list reset set"
            ;;
        "perf-budgets check")
            suggestions=""
            ;;
        "perf-budgets list")
            suggestions=""
            ;;
        "perf-budgets reset")
            suggestions=""
            ;;
        "perf-budgets set")
            suggestions=""
            ;;
        "prefs")
            suggestions="reset set show"
            ;;
        "prefs reset")
            suggestions=""
            ;;
        "prefs set")
            suggestions=""
            ;;
        "prefs show")
            suggestions=""
            ;;
        "reports")
            suggestions="delete diff list stats"
            ;;
        "reports delete")
            suggestions=""
            ;;
        "reports diff")
            suggestions=""
            ;;
        "reports list")
            suggestions=""
            ;;
        "reports stats")
            suggestions=""
            ;;
        "retries")
            suggestions="cancel cleanup list manual process"
            ;;
        "retries cancel")
            suggestions=""
            ;;
        "retries cleanup")
            suggestions=""
            ;;
        "retries list")
            suggestions=""
            ;;
        "retries manual")
            suggestions=""
            ;;
        "retries process")
            suggestions=""
            ;;
        "rules")
            suggestions="diff diff-csv export export-csv import import-csv"
            ;;
        "rules diff")
            suggestions=""
            ;;
        "rules diff-csv")
            suggestions=""
            ;;
        "rules export")
            suggestions=""
            ;;
        "rules export-csv")
            suggestions=""
            ;;
        "rules import")
            suggestions=""
            ;;
        "rules import-csv")
            suggestions=""
            ;;
        "schedules")
            suggestions="create delete disable enable list run-once"
            ;;
        "schedules create")
            suggestions=""
            ;;
        "schedules delete")
            suggestions=""
            ;;
        "schedules disable")
            suggestions=""
            ;;
        "schedules enable")
            suggestions=""
            ;;
        "schedules list")
            suggestions=""
            ;;
        "schedules run-once")
            suggestions=""
            ;;
        "settings")
            suggestions="set show"
            ;;
        "settings set")
            suggestions=""
            ;;
        "settings show")
            suggestions=""
            ;;
        "silences")
            suggestions="create delete list"
            ;;
        "silences create")
            suggestions=""
            ;;
        "silences delete")
            suggestions=""
            ;;
        "silences list")
            suggestions=""
            ;;
        "status")
            suggestions=""
            ;;
        "telemetry")
            suggestions="prune recent usage"
            ;;
        "telemetry prune")
            suggestions=""
            ;;
        "telemetry recent")
            suggestions=""
            ;;
        "telemetry usage")
            suggestions=""
            ;;
        "tokens")
            suggestions="create list revoke"
            ;;
        "tokens create")
            suggestions=""
            ;;
        "tokens list")
            suggestions=""
            ;;
        "tokens revoke")
            suggestions=""
            ;;
        "users")
            suggestions="create list"
            ;;
        "users create")
            suggestions=""
            ;;
        "users list")
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

complete -F _ops_cli_complete ops_cli
