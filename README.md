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

### macOS / Linux

```bash
git clone https://github.com/zwykstudio/claudemon-catcher.git
cd claudemon-catcher
export CLAUDEMON_API_KEY=sk_claudemon_...
./install/setup.sh
```

### Windows (PowerShell)

```powershell
git clone https://github.com/zwykstudio/claudemon-catcher.git
cd claudemon-catcher
$env:CLAUDEMON_API_KEY = "sk_claudemon_..."
.\install\setup.ps1
```

Then open a new terminal and run `cc "your prompt"`.

Get your API key at [claudemon.zwyk-studio.com/dashboard/settings](https://claudemon.zwyk-studio.com/dashboard/settings).

## Prerequisites

| Requirement | Install |
|-------------|---------|
| **Python 3.10+** | [python.org](https://www.python.org/) |
| **Claude Code** | `npm i -g @anthropic-ai/claude-code` |

The setup script handles the rest (pywinpty on Windows, shell alias, daemon).

## How It Works

```
cc "fix the auth bug"    →    wrapper.py (PTY)    →    catches.jsonl    →    engine (daemon)    →    cloud
```

`wrapper.py` captures spinner words (`✶ Reasoning…`), records duration, writes JSONL. The engine daemon syncs catches to the cloud platform.

After **3 catches** of the same word, an egg hatches. Each catch = **+1 level**, every **20 levels** = new evolution stage (6 stages, up to 100).

## Commands

```
cc "your prompt"      Catches happen automatically
cc --stats            Show stats
cc --list             List all creatures
cc --dashboard        Open cloud dashboard
```

## Configuration

| Mode | Setup | Description |
|------|-------|-------------|
| **Cloud** (default) | `export CLAUDEMON_API_KEY=sk_claudemon_...` | Dashboard, leaderboards, images |
| **Local** | `export CLAUDEMON_MODE=local` | SQLite only, no cloud sync |
| **Custom URL** | `export CLAUDEMON_CLOUD_URL=http://localhost:3000` | Point to a local/dev platform |

## MCP Server

```bash
claude mcp add claudemon -- python3 /path/to/claudemon-catcher/mcp/server.py
```

Tools: `claudemon_collection`, `claudemon_team`, `claudemon_creature`, `claudemon_recent`, `claudemon_stats`.

## Tests

```bash
python3 -m pytest tests/ -v
```

22 tests, zero network I/O. DB tests use `tmp_path` for isolation.

## Project Structure

```
wrapper.py              PTY wrapper (zero deps, pywinpty on Windows)
cli/                    CLI tools (--stats, --list, --dashboard)
engine/                 Daemon: storage sync, notifications, SQLite
mcp/                    MCP server for Claude Code
install/                Setup scripts (bash + PowerShell) + daemon configs
tests/                  Test suite (pytest)
locales/                i18n strings
```

## License

MIT
