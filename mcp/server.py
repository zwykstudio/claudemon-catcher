#!/usr/bin/env python3
"""
mcp/server.py - MCP server for Claudemon.

Exposes claudemon data to Claude Code via the Model Context Protocol.
Uses stdio transport (launched as a subprocess by Claude).

Tools:
    claudemon_collection  - List all creatures with stats
    claudemon_team        - Show active team
    claudemon_creature    - Get details for a specific creature
    claudemon_recent      - Show recent catches from this session

Setup:
    claude mcp add claudemon -- python3 /path/to/claudemon/mcp/server.py
"""

import json
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.storage import get_storage, ConfigError

CATCHES_FILE = os.path.expanduser("~/.claudemon/catches.jsonl")

TOOLS = [
    {
        "name": "claudemon_collection",
        "description": "List all captured claudemons with their level, evolution stage, and catch count. Use this to see the full collection.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sort_by": {
                    "type": "string",
                    "enum": ["level", "recent", "name", "catches"],
                    "description": "Sort order. Default: recent",
                    "default": "recent",
                }
            },
        },
    },
    {
        "name": "claudemon_team",
        "description": "Show the active team (up to 6 claudemons). The team is the user's selected favorites.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "claudemon_creature",
        "description": "Get detailed info about a specific claudemon by its word/name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "word": {
                    "type": "string",
                    "description": "The claudemon's word (e.g. 'Zigzagging')",
                }
            },
            "required": ["word"],
        },
    },
    {
        "name": "claudemon_recent",
        "description": "Show the most recent catches from the catches.jsonl file. Useful to see what was caught in the current session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Number of recent catches to show. Default: 10",
                    "default": 10,
                }
            },
        },
    },
    {
        "name": "claudemon_stats",
        "description": "Get global stats: total discovered, total catches, max level, team size, etc.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

EVOLUTION_NAMES = {1: "LARVA", 20: "SPAWN", 40: "BEAST", 60: "APEX", 80: "OMEGA", 100: "???"}


def format_creature(c: dict) -> str:
    """Format a creature for display."""
    stage_name = EVOLUTION_NAMES.get(c.get("evolution_stage", 1), "LARVA")
    egg = " (egg)" if c.get("is_egg") else ""
    team = " [TEAM]" if c.get("in_team") else ""
    return f"{c['word']} - Lv.{c['level']} ({stage_name}) - {c['times_caught']}x caught{egg}{team}"


def handle_tool(name: str, args: dict) -> str:
    """Execute a tool and return the result as text."""
    try:
        storage = get_storage()
    except ConfigError as e:
        return f"Configuration error: {e}"

    if name == "claudemon_collection":
        creatures = storage.get_all()
        if not creatures:
            return "No claudemons captured yet. Start using Claude to catch some!"

        sort_by = args.get("sort_by", "recent")
        if sort_by == "level":
            creatures.sort(key=lambda c: c["level"], reverse=True)
        elif sort_by == "name":
            creatures.sort(key=lambda c: c["word"].lower())
        elif sort_by == "catches":
            creatures.sort(key=lambda c: c["times_caught"], reverse=True)

        stats = storage.get_stats()
        lines = [f"Collection: {stats['total_discovered']} claudemons ({stats['total_hatched']} hatched, {stats['total_eggs']} eggs)", ""]
        for c in creatures:
            lines.append(format_creature(c))
        return "\n".join(lines)

    elif name == "claudemon_team":
        team = storage.get_team()
        if not team:
            return "No team members yet. Add claudemons to your team from the dashboard!"
        lines = ["Active Team:", ""]
        for i, c in enumerate(team, 1):
            lines.append(f"  {i}. {format_creature(c)}")
        return "\n".join(lines)

    elif name == "claudemon_creature":
        word = args.get("word", "")
        creature = storage.get_creature(word)
        if not creature:
            return f"No claudemon named '{word}' found."
        stage_name = EVOLUTION_NAMES.get(creature.get("evolution_stage", 1), "LARVA")
        lines = [
            f"{'[EGG] ' if creature.get('is_egg') else ''}{creature['word']}",
            f"  Level: {creature['level']}",
            f"  Stage: {stage_name} ({creature['evolution_stage']})",
            f"  Caught: {creature['times_caught']}x",
            f"  First seen: {creature.get('first_seen', 'unknown')}",
            f"  Last seen: {creature.get('last_seen', 'unknown')}",
            f"  In team: {'Yes' if creature.get('in_team') else 'No'}",
        ]
        if creature.get("is_egg"):
            progress = creature.get("hatch_progress", 0)
            lines.append(f"  Hatch progress: {progress:.0%}")
        return "\n".join(lines)

    elif name == "claudemon_recent":
        count = args.get("count", 10)
        if not os.path.exists(CATCHES_FILE):
            return "No catches yet."
        with open(CATCHES_FILE, "r") as f:
            lines = f.readlines()
        recent = lines[-count:] if len(lines) > count else lines
        if not recent:
            return "No catches yet."
        catches = []
        for line in reversed(recent):
            try:
                c = json.loads(line.strip())
                from datetime import datetime
                ts = datetime.fromtimestamp(c["ts"]).strftime("%H:%M:%S")
                catches.append(f"  {ts} - {c['word']}")
            except (json.JSONDecodeError, KeyError):
                pass
        return f"Recent catches ({len(catches)}):\n" + "\n".join(catches)

    elif name == "claudemon_stats":
        stats = storage.get_stats()
        if not stats:
            return "No stats available."
        return "\n".join([
            "Claudemon Stats:",
            f"  Discovered: {stats['total_discovered']}",
            f"  Hatched: {stats['total_hatched']}",
            f"  Eggs: {stats['total_eggs']}",
            f"  Total catches: {stats['total_catches']}",
            f"  Max level: {stats['max_level']}",
            f"  Team: {stats['team_size']}/{stats['max_team_size']}",
        ])

    return f"Unknown tool: {name}"


# === MCP Protocol (JSON-RPC over stdio) ===

def send(msg: dict):
    """Send a JSON-RPC message to stdout."""
    data = json.dumps(msg)
    sys.stdout.write(f"Content-Length: {len(data)}\r\n\r\n{data}")
    sys.stdout.flush()


def read_message() -> dict:
    """Read a JSON-RPC message from stdin."""
    headers = {}
    while True:
        line = sys.stdin.readline()
        if not line or line.strip() == "":
            break
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip()] = value.strip()

    length = int(headers.get("Content-Length", 0))
    if length == 0:
        return {}

    body = sys.stdin.read(length)
    return json.loads(body)


def main():
    """Run the MCP server (stdio transport)."""
    while True:
        try:
            msg = read_message()
            if not msg:
                break

            method = msg.get("method", "")
            msg_id = msg.get("id")

            if method == "initialize":
                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": "claudemon",
                            "version": "1.0.0",
                        },
                    },
                })

            elif method == "notifications/initialized":
                pass  # No response needed

            elif method == "tools/list":
                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"tools": TOOLS},
                })

            elif method == "tools/call":
                params = msg.get("params", {})
                tool_name = params.get("name", "")
                tool_args = params.get("arguments", {})

                try:
                    result_text = handle_tool(tool_name, tool_args)
                    send({
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [{"type": "text", "text": result_text}],
                        },
                    })
                except Exception as e:
                    send({
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [{"type": "text", "text": f"Error: {e}"}],
                            "isError": True,
                        },
                    })

            elif msg_id is not None:
                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Unknown method: {method}"},
                })

        except (EOFError, KeyboardInterrupt):
            break
        except Exception as e:
            sys.stderr.write(f"[mcp] Error: {e}\n")


if __name__ == "__main__":
    main()
