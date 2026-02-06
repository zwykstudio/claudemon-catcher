<p align="center">
  <img src="claudemon.svg" alt="Claudemon" width="200" />
</p>

<h1 align="center">Claudemon Catcher</h1>

<p align="center">
  <strong>Gotta catch 'em all — while you code.</strong>
  <br />
  Every time Claude thinks, a wild Claudemon appears. Capture spinner words, hatch eggs, evolve creatures.
</p>

---

## Quick Start

```bash
git clone https://github.com/zwykstudio/claudemon-catcher.git
cd claudemon-catcher

# Set your API key (get one at https://claudemon.zwyk-studio.com)
export CLAUDEMON_API_KEY=sk_claudemon_...

# Install (sets up engine daemon + shell alias)
./install/setup.sh

# Use Claude as usual — the wrapper does the rest
cc "your prompt here"
```

## Prerequisites

| Requirement | Check | Install |
|-------------|-------|---------|
| **Python 3.10+** | `python3 --version` | [python.org](https://www.python.org/) |
| **Claude Code** | `which claude` | `npm i -g @anthropic-ai/claude-code` |

**Platforms:** macOS, Linux, Windows (via WSL or manual `wrapper.py`).

Optional: `brew install terminal-notifier` (macOS) or `sudo apt install libnotify` (Linux) for native desktop notifications.

## How It Works

```
cc "fix the auth bug"
        │
        ▼
  wrapper.py (PTY)     Intercepts spinner words, writes catches
        │
        ▼
  ~/.claudemon/catches.jsonl
        │
        ▼
  engine (daemon)      Processes catches, sends notifications
        │
        ▼
  Cloud platform       https://claudemon.zwyk-studio.com
```

`wrapper.py` captures words matching the spinner pattern (`✶ Reasoning…`), records their display duration, and writes one JSON line per catch. The engine daemon syncs to the cloud platform.

After **3 catches** of the same word, an egg hatches. Each additional catch = **+1 level**, every **20 levels** = new evolution stage (6 stages total, up to level 100).

### Catch format

```jsonl
{"word": "Reasoning", "ts": 1738765432.1, "duration": 3.241, "proof": "a1b2c3d4e5f6a7b8", "sid": "abc123def456"}
```

## MCP Server

Let Claude see your collection during conversations:

```bash
claude mcp add claudemon -- python3 /path/to/claudemon-catcher/mcp/server.py
```

Tools: `claudemon_collection`, `claudemon_team`, `claudemon_creature`, `claudemon_recent`, `claudemon_stats`.

## Configuration

### Cloud mode (default)

```bash
export CLAUDEMON_API_KEY=sk_claudemon_...
```

Dashboard, images, leaderboards — all handled by the platform.

### Local mode (opt-in)

```bash
export CLAUDEMON_MODE=local
# Do NOT set CLAUDEMON_API_KEY in local mode
```

**Webhook (local mode only):**

```bash
export CLAUDEMON_WEBHOOK_URL=http://localhost:8080/webhook
```

## Commands

```bash
cc "your prompt"      # Catches happen automatically
cc --stats            # Show stats
cc --list             # List all creatures
cc --dashboard        # Open cloud dashboard
```

## Project Structure

```
claudemon-catcher/
├── wrapper.py            # PTY wrapper (zero deps)
├── cli/                  # CLI tools (--stats, --list, --dashboard)
├── engine/
│   ├── engine.py         # Watches catches.jsonl
│   ├── storage.py        # Local (SQLite) or Cloud (API) adapter
│   ├── database.py       # SQLite operations
│   └── notifications.py  # OS notifications + webhook
├── mcp/                  # MCP server for Claude Code
├── tests/                # Test suite (pytest)
├── install/              # Setup script + daemon configs
└── locales/              # i18n strings
```

Data lives in `~/.claudemon/` (catches.jsonl, claudemon.db).

## Tests

```bash
python3 -m pytest tests/ -v
```

17 tests couvrent la logique metier sans network I/O :

| Module | Tests | Couverture |
|--------|-------|------------|
| **Wrapper** | 7 | Regex spinner, detection/emission de mots, dedup, flush timeout, format JSONL |
| **Database** | 5 | Stades d'evolution, catch new/level-up/hatch/evolve, gestion d'equipe |
| **Storage** | 3 | Routing cloud/local, erreurs de config |
| **MCP** | 2 | Formatage creature, valeurs par defaut |

Les tests DB utilisent `tmp_path` (SQLite isole), les tests wrapper ecrivent dans des fichiers temporaires.

## License

MIT
