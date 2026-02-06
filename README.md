<p align="center">
  <img src="claudemon.svg" alt="Claudemon" width="200" />
</p>

<h1 align="center">Claudemon Catcher</h1>

<p align="center">
  <strong>Gotta catch 'em all — while you code.</strong>
  <br />
  Every time Claude thinks, a wild Claudemon appears. Capture spinner words, hatch eggs, evolve creatures.
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> &bull;
  <a href="#how-it-works">How It Works</a> &bull;
  <a href="#evolution-stages">Evolutions</a> &bull;
  <a href="#mcp-server">MCP Server</a> &bull;
  <a href="#cloud-mode">Cloud Mode</a>
</p>

---

## What is this?

When you use [Claude Code](https://docs.anthropic.com/en/docs/claude-code), the CLI shows a spinner with random words while Claude is thinking: `✶ Reasoning...`, `✻ Analyzing...`, `✽ Exploring...`

**Claudemon Catcher** intercepts those words and turns them into collectible creatures. Each word is a unique Claudemon that hatches from an egg, levels up with every catch, and evolves through 6 stages.

It's a zero-friction idle game that runs in the background while you work. No extra commands, no interruptions — just code normally and watch your collection grow.

## Quick Start

```bash
# Clone
git clone https://github.com/zwykstudio/claudemon-catcher.git
cd claudemon-catcher

# Install (sets up daemons + shell alias)
./install/setup.sh

# Use Claude as usual — the wrapper does the rest
cc "your prompt here"

# Check your collection
open http://localhost:17712
```

## Prerequisites

| Requirement | Check | Install |
|-------------|-------|---------|
| **macOS** | Required | — |
| **Python 3.10+** | `python3 --version` | [python.org](https://www.python.org/) |
| **Claude Code** | `which claude` | `npm i -g @anthropic-ai/claude-code` |
| **GitHub CLI** | `gh --version` | `brew install gh` |

Optional: `brew install terminal-notifier` for native macOS notifications.

## How It Works

```
You type: cc "fix the auth bug"
                │
                ▼
┌──────────────────────────────┐
│  wrapper.py (PTY intercept)  │  Transparent proxy — Claude works normally
│  Captures: Reasoning,        │  You see the exact same output
│  Analyzing, Exploring...     │
└──────────────┬───────────────┘
               │ writes to
               ▼
  ~/.claudemon/catches.jsonl      One JSON line per catch
               │
               │ watched by
               ▼
┌──────────────────────────────┐
│  engine (background daemon)  │  Processes catches → updates DB
│  Sends macOS notifications   │  "A wild Reasoning appeared!"
└──────────────┬───────────────┘
               │
               ▼
      Local dashboard              http://localhost:17712
      or Cloud platform            https://claudemon.zwyk-studio.com
```

1. `wrapper.py` intercepts Claude's spinner output via PTY (zero dependencies, ~200 LOC)
2. Extracts words matching the spinner pattern
3. Writes catches to `~/.claudemon/catches.jsonl`
4. Engine daemon picks up catches and updates storage
5. After **3 catches** of the same word, an egg hatches into a creature
6. Each additional catch = **+1 level**, every **20 levels** = evolution

## Evolution Stages

| Level | Stage | |
|-------|-------|-|
| 1–19 | **Larva** | Just hatched, a tiny blob of potential |
| 20–39 | **Spawn** | Growing, finding its shape |
| 40–59 | **Beast** | Getting strong, recognizable form |
| 60–79 | **Apex** | Powerful, fully realized |
| 80–99 | **Omega** | Near perfection |
| 100 | **???** | Ultimate form. Only the most dedicated trainers. |

## MCP Server

Let Claude see your collection during conversations:

```bash
claude mcp add claudemon -- python3 /path/to/claudemon-catcher/mcp/server.py
```

Tools available to Claude:
- `claudemon_collection` — List all your creatures
- `claudemon_team` — Show your active team
- `claudemon_creature` — Details on a specific creature
- `claudemon_recent` — Recent catches
- `claudemon_stats` — Global stats

## Cloud Mode

Sync your collection to the cloud platform and compete with others:

```bash
export CLAUDEMON_MODE=cloud
export CLAUDEMON_API_KEY=sk_claudemon_...    # get one at https://claudemon.zwyk-studio.com
export CLAUDEMON_CLOUD_URL=https://claudemon.zwyk-studio.com
```

## Commands

```bash
# Use Claude (catches happen automatically)
cc "your prompt"

# CLI
python3 cli/main.py --stats        # Show stats
python3 cli/main.py --list         # List all creatures
python3 cli/main.py --dashboard    # Open web dashboard

# Daemon management
launchctl list | grep claudemon
tail -f /tmp/claudemon-engine.log
tail -f /tmp/claudemon-server.log
```

## Data

All local data lives in `~/.claudemon/`:

```
~/.claudemon/
├── catches.jsonl     # Append-only catch log
└── claudemon.db      # SQLite database
```

## Project Structure

```
claudemon-catcher/
├── wrapper.py            # PTY wrapper (zero deps, ~200 LOC)
├── cli/                  # CLI tools (--stats, --list, --dashboard)
├── engine/               # Game engine daemon
│   ├── engine.py         # Watches catches.jsonl
│   ├── storage.py        # Local (SQLite) or Cloud (API) adapter
│   ├── database.py       # SQLite operations
│   ├── notifications.py  # macOS + web notifications
│   └── cloud.py          # Cloud API client
├── server/               # Local HTTP server + dashboard
├── mcp/                  # MCP server for Claude Code
├── install/              # Setup script + launchd plists
└── locales/              # i18n strings
```

## License

MIT
