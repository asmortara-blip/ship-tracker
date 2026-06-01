# Tab-completion install

Auto-generated. Regenerate via:

```bash
python -m tools.completion_cli all --out-dir docs/completion
```

## bash

```bash
source docs/completion/anonymize_cli.bash
source docs/completion/backup_cli.bash
source docs/completion/changelog_cli.bash
source docs/completion/db_check_cli.bash
source docs/completion/ops_cli.bash
source docs/completion/replay_cli.bash
source docs/completion/schema_docs_cli.bash

# Or persist for all users on the host:
sudo cp docs/completion/anonymize_cli.bash /etc/bash_completion.d/
sudo cp docs/completion/backup_cli.bash /etc/bash_completion.d/
sudo cp docs/completion/changelog_cli.bash /etc/bash_completion.d/
sudo cp docs/completion/db_check_cli.bash /etc/bash_completion.d/
sudo cp docs/completion/ops_cli.bash /etc/bash_completion.d/
sudo cp docs/completion/replay_cli.bash /etc/bash_completion.d/
sudo cp docs/completion/schema_docs_cli.bash /etc/bash_completion.d/
```

## zsh

```bash
# Add docs/completion to your $fpath, then autoload + compinit:
fpath=("$PWD/docs/completion" $fpath)
autoload -U _anonymize_cli
autoload -U _backup_cli
autoload -U _changelog_cli
autoload -U _db_check_cli
autoload -U _ops_cli
autoload -U _replay_cli
autoload -U _schema_docs_cli
compinit
```

## Notes

* Completion only covers subcommand names — option *values* are
  not completed (operators just type the id).
* If you run a CLI via ``python -m tools.ops_cli`` the bash
  ``complete -F`` hook binds to argv[0] (``python``), so the
  hook won't fire. Wrap the invocation in a shell function or
  drop a ``ops_cli`` wrapper script on ``$PATH`` to use
  completion in that mode.
