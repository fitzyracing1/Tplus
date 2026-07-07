#!/usr/bin/env python3
"""Launch a Cursor Cloud Agent on every one of your repos, all running the same task.

Subcommands:
  repos    Fetch the list of repos your Cursor account can access and write repos.txt
  launch   Launch one cloud agent per repo in repos.txt, using the prompt in prompt.md
  status   Show the status of agents previously launched by this tool

Auth: set the CURSOR_API_KEY environment variable (Cursor Dashboard -> API Keys).

Examples:
  export CURSOR_API_KEY=key_...
  python3 fleet.py repos                 # populate repos.txt (edit it to trim the list)
  python3 fleet.py launch --dry-run      # preview what would be launched
  python3 fleet.py launch --yes          # actually launch (one agent per repo)
  python3 fleet.py status                # check on the launched agents
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_BASE = "https://api.cursor.com/v1"
HERE = os.path.dirname(os.path.abspath(__file__))
REPOS_FILE = os.path.join(HERE, "repos.txt")
PROMPT_FILE = os.path.join(HERE, "prompt.md")
STATE_FILE = os.path.join(HERE, "launched.json")


def api_key():
    key = os.environ.get("CURSOR_API_KEY")
    if not key:
        sys.exit(
            "CURSOR_API_KEY is not set. Create a key at cursor.com/dashboard -> "
            "API Keys, then: export CURSOR_API_KEY=key_..."
        )
    return key


def request(method, path, body=None):
    url = API_BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key()}",
            "Content-Type": "application/json",
        },
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if e.code == 429 and attempt < 4:
                wait = 2 ** (attempt + 2)
                print(f"  rate limited, retrying in {wait}s ...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise RuntimeError(f"{method} {path} failed ({e.code}): {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < 4:
                wait = 2 ** (attempt + 2)
                print(f"  network error ({e}), retrying in {wait}s ...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise


def read_repos():
    if not os.path.exists(REPOS_FILE):
        sys.exit(f"{REPOS_FILE} not found. Run `python3 fleet.py repos` or create it by hand.")
    repos = []
    with open(REPOS_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                repos.append(line)
    if not repos:
        sys.exit(f"{REPOS_FILE} contains no repos (lines starting with # are ignored).")
    return repos


def read_prompt():
    if not os.path.exists(PROMPT_FILE):
        sys.exit(f"{PROMPT_FILE} not found. Put the task you want every agent to do in it.")
    prompt = open(PROMPT_FILE).read().strip()
    if not prompt or "REPLACE THIS" in prompt:
        sys.exit(f"Edit {PROMPT_FILE} first: describe the task you want run on every repo.")
    return prompt


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return []


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def cmd_repos(args):
    print("Fetching repositories from Cursor (can take tens of seconds) ...")
    data = request("GET", "/repositories")
    urls = sorted(item["url"] for item in data.get("items", []))
    if not urls:
        sys.exit("No repositories returned. Check your Cursor GitHub App installation.")
    with open(REPOS_FILE, "w") as f:
        f.write("# One repo URL per line. Comment out (#) any repo you want to skip.\n")
        for url in urls:
            f.write(url + "\n")
    print(f"Wrote {len(urls)} repos to {REPOS_FILE}. Review and trim it before launching.")


def cmd_launch(args):
    if not args.dry_run:
        api_key()
    repos = read_repos()
    prompt = read_prompt()
    if args.limit:
        repos = repos[: args.limit]

    print(f"Prompt ({PROMPT_FILE}):\n  " + prompt.replace("\n", "\n  ")[:500] + "\n")
    print(f"Repos to launch ({len(repos)}):")
    for url in repos:
        print(f"  {url}")

    if args.dry_run:
        print("\nDry run: nothing launched.")
        return
    if not args.yes:
        sys.exit(
            f"\nThis will launch {len(repos)} cloud agents (one per repo), which uses "
            "credits. Re-run with --yes to confirm, or --dry-run to preview."
        )

    state = load_state()
    already = {entry["repo"] for entry in state} if args.skip_launched else set()
    launched = failed = skipped = 0
    for url in repos:
        if url in already:
            print(f"skip (already launched): {url}")
            skipped += 1
            continue
        repo_entry = {"url": url}
        if args.ref:
            repo_entry["startingRef"] = args.ref
        body = {
            "prompt": {"text": prompt},
            "repos": [repo_entry],
            "autoCreatePR": not args.no_pr,
        }
        if args.model:
            body["model"] = {"id": args.model}
        while True:
            try:
                resp = request("POST", "/agents", body)
                agent = resp.get("agent", resp)
                agent_id = agent.get("id", "?")
                print(f"launched {agent_id}: {url}", flush=True)
                state.append(
                    {
                        "repo": url,
                        "agentId": agent_id,
                        "launchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                )
                save_state(state)
                launched += 1
                break
            except RuntimeError as e:
                # Plans cap how many Cloud Agents may run simultaneously. When the
                # cap is hit, wait for running agents to finish and retry the same
                # repo instead of failing it.
                if "reached the limit" in str(e) or "Upgrade to Ultra" in str(e):
                    print(
                        f"concurrency limit reached; waiting {args.wait}s for a free slot ...",
                        flush=True,
                    )
                    time.sleep(args.wait)
                    continue
                if "usage_limit_exceeded" in str(e):
                    if args.budget_wait:
                        print(
                            f"spend limit reached; retrying in {args.budget_wait}s "
                            "(raise it at cursor.com/dashboard -> Settings) ...",
                            flush=True,
                        )
                        time.sleep(args.budget_wait)
                        continue
                    print(
                        f"\nSTOPPED: the account is out of budget for Cloud Agents.\n{e}\n"
                        "Enable usage-based pricing / raise the spend limit at "
                        "cursor.com/dashboard -> Settings, then resume with:\n"
                        "  python3 fleet.py launch --yes --skip-launched",
                        file=sys.stderr,
                        flush=True,
                    )
                    print(f"\nDone so far: {launched} launched, {failed} failed, {skipped} skipped.")
                    return
                print(f"FAILED {url}: {e}", file=sys.stderr, flush=True)
                failed += 1
                break
        time.sleep(args.delay)
    print(f"\nDone: {launched} launched, {failed} failed, {skipped} skipped.")
    print(f"Agent IDs recorded in {STATE_FILE}. Track them with `python3 fleet.py status`.")


def cmd_status(args):
    state = load_state()
    if not state:
        sys.exit(f"No launches recorded in {STATE_FILE} yet.")
    width = max(len(e["repo"]) for e in state)
    counts = {}
    for entry in state:
        try:
            # Agent status only says whether the agent is archived; the latest
            # run's status is what tells us if the work is done.
            runs = request("GET", f"/agents/{entry['agentId']}/runs?limit=1")
            items = runs.get("items", [])
            status = items[0].get("status", "?") if items else "NO_RUNS"
            agent = request("GET", f"/agents/{entry['agentId']}")
            branches = agent.get("git", {}).get("branches") or []
            extra = f"  branch: {branches[0]}" if branches else ""
        except RuntimeError as e:
            status, extra = "ERROR", f"  {e}"
        counts[status] = counts.get(status, 0) + 1
        print(f"{entry['repo']:<{width}}  {entry['agentId']}  {status}{extra}", flush=True)
    print("\nSummary: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("repos", help="fetch accessible repos into repos.txt")

    p_launch = sub.add_parser("launch", help="launch one agent per repo in repos.txt")
    p_launch.add_argument("--dry-run", action="store_true", help="preview without launching")
    p_launch.add_argument("--yes", action="store_true", help="confirm launching (required)")
    p_launch.add_argument("--limit", type=int, help="only launch the first N repos")
    p_launch.add_argument("--ref", help="starting branch (default: each repo's default branch)")
    p_launch.add_argument("--model", help="model id, e.g. composer-2 (default: your Cursor default)")
    p_launch.add_argument("--no-pr", action="store_true", help="don't auto-create PRs")
    p_launch.add_argument("--delay", type=float, default=2.0, help="seconds between launches (default: 2)")
    p_launch.add_argument(
        "--wait",
        type=float,
        default=120.0,
        help="seconds to wait when the plan's concurrent-agent limit is hit (default: 120)",
    )
    p_launch.add_argument(
        "--budget-wait",
        type=float,
        default=0,
        help="if set, retry every N seconds when the spend limit is hit instead of stopping",
    )
    p_launch.add_argument(
        "--skip-launched",
        action="store_true",
        help="skip repos already recorded in launched.json (safe re-run)",
    )

    sub.add_parser("status", help="show status of previously launched agents")

    args = parser.parse_args()
    {"repos": cmd_repos, "launch": cmd_launch, "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    main()
