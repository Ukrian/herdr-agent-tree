# herdr agent tree

A [herdr](https://herdr.dev) plugin that shows the sidebar's agents panel as a
tree: each space's name on its own line, with that space's agents listed under
it.

```
infra
   - ● fix-login  claude
   - ● review-pr  codex
web
   - ● docs       claude
```

herdr can't draw group headers in the agents panel, and plugins can't draw in
the sidebar. Agent rows *can* show custom `$name` tokens attached to each pane,
though, and herdr drops a row when all of its tokens are missing. The plugin
attaches:

| Token | Set on | Value |
| --- | --- | --- |
| `$tree_space` | the first agent of each space | the space name |
| `$tree_branch` | every agent | a `-` bullet, padded so all agents line up |
| `$agent_short` | every agent | the agent name, or your abbreviation for it |

Your `config.toml` decides where those tokens appear. The plugin updates them
whenever an agent appears or quits, or a pane, tab or space is closed, moved
or renamed. It also rebuilds them at server startup, because herdr doesn't
keep these values across restarts.

## Requirements

- herdr 0.9.5 or newer
- Linux or macOS
- `python3` on your `PATH` (standard library only)

## Install

```bash
herdr plugin install Ukrian/herdr-agent-tree
```

Then add these agent rows to `~/.config/herdr/config.toml`. If you already have
a `[ui.sidebar.agents]` section, replace its `rows`.

```toml
[ui.sidebar.agents]
rows = [
  [{ token = "$tree_space", bold = true }],
  [{ token = "$tree_branch", dim = true }, "state_icon", "tab", { token = "$agent_short", dim = true }],
]
```

Apply it and build the tree for the agents that are already running:

```bash
herdr server reload-config
herdr plugin action invoke ukrian.agent-tree.refresh
```

## Shorten agent names

To show `c` instead of `claude`, create `abbreviations.json` in the plugin's
config directory:

```bash
echo '{"claude": "c", "codex": "x"}' > "$(herdr plugin config-dir ukrian.agent-tree)/abbreviations.json"
herdr plugin action invoke ukrian.agent-tree.refresh
```

Keys are herdr's agent ids (`claude`, `codex`, `pi`, ...). Agents that aren't
listed keep their full name. The file is read on every update.

## Limits

- **Keep the panel in "grouped" order.** The tree follows the space order of
  the agents list. Clicking the "grouped" label in the panel header switches
  to "priority" order and scrambles the tree; click it again to go back.
- **No folding.** A space name is part of its first agent's entry, so clicking
  it jumps to that agent. herdr has no way for a plugin to fold a space.
- **Brief lag.** Right after an agent appears or quits, its row can show
  without its tokens until the update lands, usually within a fraction of a
  second.
- **The padding is a character.** All agents line up because every agent
  after the first in a space gets two Braille blank characters (U+2800) before
  its `-`, since herdr strips leading spaces from token values. If your font
  draws them as faint dots, open an issue.

## Remove

```bash
herdr plugin action invoke ukrian.agent-tree.clear
herdr plugin uninstall ukrian.agent-tree
```

Then remove or replace the `[ui.sidebar.agents]` rows above. herdr's default
is:

```toml
rows = [["state_icon", "machine", "workspace", "tab"], ["agent"]]
```

## Troubleshooting

```bash
herdr plugin log list --plugin ukrian.agent-tree
```

Every update writes problems to stderr, and herdr keeps them in that log.

## License

MIT, see [LICENSE](LICENSE).
