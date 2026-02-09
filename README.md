<p align="center">
  <img src="claudemon.svg" alt="Claudemon" width="200" />
</p>

<h1 align="center">Claudemon Catcher</h1>

<p align="center">
  <strong>Gotta catch 'em all — while you code.</strong>
  <br />
  Every time Claude thinks, a wild Claudemon appears.<br/>
  Capture spinner words, hatch eggs, evolve creatures, climb leaderboards.
</p>

---

## Quick Start

```bash
git clone https://github.com/zwykstudio/claudemon-catcher.git
cd claudemon-catcher
export CLAUDEMON_API_KEY=sk_claudemon_...   # get yours at claudemon.zwyk-studio.com
./install/setup.sh                           # Windows: .\install\setup.ps1
```

Then open a new terminal and use `cc` instead of `claude`:

```bash
cc "fix the auth bug"
```

Catches happen automatically. That's it.

## How It Works

```
you type cc "..."  →  wrapper catches spinner words  →  engine syncs to cloud
                          ✶ Reasoning… = catch!
```

- **3 catches** of the same word → egg hatches
- Each catch → **+1 level**
- Every **20 levels** → evolution (6 stages, up to lvl 100)

The statusline shows catches in real-time in Claude Code's status bar:

```
Opus 12% | [Claudemon] Reasoning +15xp 7.2s NEW (3 caught · 45xp)
```

## Commands

```
cc "your prompt"           Normal usage — catches happen automatically
cc --stats                 Show your stats
cc --list                  List all your creatures
cc --dashboard             Open cloud dashboard
cc --install-statusline    Configure the statusline
cc engine                  Show engine daemon status
cc engine restart          Restart the engine daemon
cc engine update-key [K]   Update API key in daemon config
cc engine reset            Reset engine state and restart
```

## Configuration

| Variable | Description |
|----------|-------------|
| `CLAUDEMON_API_KEY` | Your API key (get it from [the dashboard](https://claudemon.zwyk-studio.com/dashboard/settings)) |
| `CLAUDEMON_MODE=local` | SQLite only, no cloud sync |
| `CLAUDEMON_CLOUD_URL` | Point to a custom/dev platform |

## MCP Server

Give Claude access to your collection:

```bash
claude mcp add claudemon -- python3 /path/to/claudemon-catcher/mcp/server.py
```

Tools: `claudemon_collection`, `claudemon_team`, `claudemon_creature`, `claudemon_recent`, `claudemon_stats`.

## Prerequisites

- **Python 3.10+**
- **Claude Code** (`npm i -g @anthropic-ai/claude-code`)

The setup script handles the rest.

## License

MIT
