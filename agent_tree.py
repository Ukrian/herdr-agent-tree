#!/usr/bin/env python3
"""Draw herdr's agents panel as a space -> agents tree.

herdr cannot render group headers in the agents panel, but agent rows can show
custom `$name` tokens taken from pane metadata, and a row whose tokens are all
missing is dropped. This script exploits that: it tags the first agent of every
space with `$tree_space` (the space name, shown as a header line) and every
agent with `$tree_branch` (a `-` bullet) and `$agent_short` (the agent name,
optionally abbreviated). The user's config.toml decides where those tokens
appear; see README.md.

Usage: agent_tree.py sync   recompute and apply the tokens (default)
       agent_tree.py clear  remove every token this plugin set

Abbreviations are read from `abbreviations.json` in the plugin config
directory (`herdr plugin config-dir ukrian.agent-tree`), e.g. {"claude": "c"}.

The tree only lines up while the agents panel uses the "grouped" (spaces)
order, because that is the order of `agents` in the session snapshot.
"""

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")
PLUGIN_ID = os.environ.get("HERDR_PLUGIN_ID", "ukrian.agent-tree")
SOURCE = f"plugin:{PLUGIN_ID}"
# herdr sets both directories for hooks and actions; the fallbacks only matter
# when the script is run by hand.
STATE_DIR = Path(
    os.environ.get("HERDR_PLUGIN_STATE_DIR")
    or Path.home() / ".cache" / "herdr-agent-tree"
)
CONFIG_DIR = Path(
    os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    or Path.home() / ".config" / "herdr-agent-tree"
)

SPACE_TOKEN = "tree_space"
BRANCH_TOKEN = "tree_branch"
BRANCH = "-"
# herdr indents an entry's first line by 1 column and later lines by 3. The
# first agent of a space has the header as line 1, so its bullet line sits 2
# columns right of every other agent's. Pad the others by those 2 columns.
# Braille blanks (U+2800), not spaces: herdr trims whitespace off token values.
BRANCH_PADDED = "⠀⠀" + BRANCH
# Rows show $agent_short instead of the built-in `agent` token: rules can only
# restyle or hide a token, not rewrite its text.
AGENT_TOKEN = "agent_short"
ABBREVIATIONS_FILE = CONFIG_DIR / "abbreviations.json"
# herdr caps token values at 80 characters; compare against the same cap so a
# long space name doesn't look "changed" on every run and get re-sent forever.
TOKEN_MAX_LEN = 80


def log(message):
    """Write a diagnostic line to stderr, which herdr keeps in the plugin log."""
    print(f"agent-tree: {message}", file=sys.stderr)


def herdr(*args):
    """Run a herdr CLI command and return its stdout.

    Raises:
        subprocess.CalledProcessError: if herdr exits non-zero.
    """
    return subprocess.run(
        [HERDR, *args], check=True, capture_output=True, text=True
    ).stdout


def load_snapshot():
    """Return the live session snapshot (workspaces, tabs, panes, agents)."""
    return json.loads(herdr("api", "snapshot"))["result"]["snapshot"]


def load_abbreviations():
    """Return the user's {agent id: short name} map, or {} when there is none.

    A broken file is logged and ignored rather than raised: this runs from
    event hooks, and failing there would stop the whole tree from updating.
    """
    try:
        data = json.loads(ABBREVIATIONS_FILE.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:
        log(f"ignoring {ABBREVIATIONS_FILE}: {err}")
        return {}
    if not isinstance(data, dict):
        log(f"ignoring {ABBREVIATIONS_FILE}: expected a JSON object")
        return {}
    return {str(key): str(value) for key, value in data.items()}


def desired_tokens(snapshot, abbreviations):
    """Compute the tree tokens every agent pane should carry.

    Args:
        snapshot: the `session.snapshot` payload. Its `agents` list is ordered
            by space, then tab, then pane: the same order the agents panel
            uses in "grouped" mode, which is what makes the glyphs line up.
        abbreviations: {agent id: short name}; agents not listed keep their
            full name.

    Returns:
        A list of (pane_id, current_tokens, wanted) tuples, where `wanted`
        maps token name to a value, or to None when the token must be cleared.
    """
    labels = {ws["workspace_id"]: ws.get("label", "") for ws in snapshot["workspaces"]}
    agents = snapshot["agents"]
    result = []
    for index, agent in enumerate(agents):
        space = agent["workspace_id"]
        first = index == 0 or agents[index - 1]["workspace_id"] != space
        header = (labels.get(space) or space).strip()[:TOKEN_MAX_LEN] if first else None
        name = agent.get("agent")
        wanted = {
            SPACE_TOKEN: header,
            BRANCH_TOKEN: BRANCH if first else BRANCH_PADDED,
            AGENT_TOKEN: abbreviations.get(name, name),
        }
        result.append((agent["pane_id"], agent.get("tokens") or {}, wanted))
    return result


def report(pane_id, current, wanted):
    """Send only the token changes one pane needs, as a single metadata report.

    Panes can vanish between the snapshot and this call; that error is logged
    and skipped because the pane.closed event that follows triggers a new sync.
    """
    args = []
    for name, value in wanted.items():
        if value is None:
            if name in current:
                args += ["--clear-token", name]
        elif current.get(name) != value:
            args += ["--token", f"{name}={value}"]
    if not args:
        return
    try:
        # The pane id must come before the options: herdr 0.9.5's parser
        # rejects it as "unknown option" when it follows --source, despite
        # what `--help` shows.
        herdr("pane", "report-metadata", pane_id, "--source", SOURCE, *args)
    except subprocess.CalledProcessError as err:
        log(f"report for {pane_id} failed: {err.stderr.strip()}")


def sync():
    """Bring every agent's tree tokens in line with the current layout."""
    for pane_id, current, wanted in desired_tokens(load_snapshot(), load_abbreviations()):
        report(pane_id, current, wanted)


def clear():
    """Remove this plugin's tokens from every agent, restoring plain rows."""
    for agent in load_snapshot()["agents"]:
        report(
            agent["pane_id"],
            agent.get("tokens") or {},
            {SPACE_TOKEN: None, BRANCH_TOKEN: None, AGENT_TOKEN: None},
        )


def coalesced(work):
    """Run `work` so that a burst of hook invocations costs one or two runs.

    herdr starts a separate process for every event (status changes can fire
    many per second across 30 agents) and caps plugin commands at 32 in
    flight, so blocking on a lock would pile processes up. Instead each caller
    raises a `pending` flag; whoever holds the lock keeps re-running `work`
    until the flag stays down, and everyone else exits immediately.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    pending = STATE_DIR / "pending"
    pending.touch()
    with open(STATE_DIR / "lock", "w") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # The holder re-checks `pending` after releasing, so our
                # request is never lost.
                return
            try:
                while pending.exists():
                    pending.unlink(missing_ok=True)
                    work()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
            # A caller may have raised the flag after our last check but
            # before we unlocked, and then failed to get the lock. Go again.
            if not pending.exists():
                return


def main():
    """Dispatch the `sync` / `clear` subcommand."""
    command = sys.argv[1] if len(sys.argv) > 1 else "sync"
    if command == "sync":
        coalesced(sync)
    elif command == "clear":
        # Run clear under the same lock so a concurrent sync can't re-add
        # tokens halfway through.
        coalesced(clear)
    else:
        log(f"unknown command {command!r}; expected sync or clear")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
