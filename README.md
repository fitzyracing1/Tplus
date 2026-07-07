# Tplus — Agent Fleet Launcher

Launch a Cursor Cloud Agent on **every one of your repos**, all running the
same task. Each agent clones its repo, does the work, and (by default) opens a
pull request there.

## One-time setup

1. **API key** — go to [cursor.com/dashboard](https://cursor.com/dashboard) →
   *API Keys* and create a key, then:

   ```bash
   export CURSOR_API_KEY=key_...
   ```

2. **Describe the task** — edit `prompt.md` and replace the placeholder with
   the task you want every agent to perform. Every agent gets this exact text
   as its instructions.

3. **Pick the repos** — `repos.txt` is pre-populated with your GitHub repos
   (252 of them). Delete or comment out (`#`) any you want to skip — backups,
   scratch folders, and copies are probably not worth an agent run. You can
   refresh the list from Cursor at any time:

   ```bash
   python3 fleet.py repos
   ```

## Launching

```bash
python3 fleet.py launch --dry-run          # preview: prompt + repo list, launches nothing
python3 fleet.py launch --yes --limit 3    # trial run on the first 3 repos
python3 fleet.py launch --yes              # the whole fleet, one agent per repo
python3 fleet.py status                    # check on every launched agent
```

Each launch is recorded in `launched.json`, so if a run is interrupted you can
resume without duplicates:

```bash
python3 fleet.py launch --yes --skip-launched
```

### Options

| Flag | Effect |
| --- | --- |
| `--dry-run` | Show what would be launched, launch nothing |
| `--yes` | Required confirmation to actually launch |
| `--limit N` | Only launch the first N repos in `repos.txt` |
| `--ref BRANCH` | Start from a specific branch (default: each repo's default branch) |
| `--model ID` | Model to use, e.g. `composer-2` (default: your Cursor default) |
| `--no-pr` | Push a branch but don't auto-open a pull request |
| `--delay SECS` | Pause between launches, default 2s |
| `--skip-launched` | Skip repos already recorded in `launched.json` |

## Notes

- Every agent run consumes Cursor credits. With ~250 repos in `repos.txt`, a
  full launch is ~250 agent runs — trim the list and use `--limit` for a trial
  first.
- Repos must be accessible to Cursor's GitHub App. `python3 fleet.py repos`
  shows exactly which ones are; anything missing needs the GitHub App
  installed/granted on it.
- No dependencies beyond Python 3 (standard library only).
