#!/bin/bash
#
# Claudemon statusline for Claude Code
#
# Reads ~/.claudemon/statusline.json (written by the engine after each catch)
# and displays a one-liner in the Claude Code status bar.
#
# Install:
#   claude config set statusline "bash /path/to/claudemon-catcher/install/statusline.sh"
#

STATUSLINE_FILE="$HOME/.claudemon/statusline.json"

# Read Claude Code session data from stdin
input=$(cat)

# Base info from Claude Code
MODEL=$(echo "$input" | jq -r '.model.display_name // empty' 2>/dev/null)
PCT=$(echo "$input" | jq -r '.context_window.used_percentage // 0' 2>/dev/null | cut -d. -f1)

base=""
[ -n "$MODEL" ] && base="$MODEL"
[ -n "$PCT" ] && [ "$PCT" != "0" ] && base="$base ${PCT}%"

# No statusline file yet — engine hasn't caught anything
if [ ! -f "$STATUSLINE_FILE" ]; then
  echo "$base"
  exit 0
fi

# Read catch data
data=$(cat "$STATUSLINE_FILE" 2>/dev/null)
if [ -z "$data" ]; then
  echo "$base"
  exit 0
fi

word=$(echo "$data" | jq -r '.word // empty')
is_new=$(echo "$data" | jq -r '.is_new // false')
level=$(echo "$data" | jq -r '.level // 1')
evolved=$(echo "$data" | jq -r '.evolved // false')
hatched=$(echo "$data" | jq -r '.just_hatched // false')
is_egg=$(echo "$data" | jq -r '.is_egg // false')
count=$(echo "$data" | jq -r '.session_catches // 0')
ts=$(echo "$data" | jq -r '.ts // 0')

# Check staleness — hide after 5 minutes of no catches
now=$(date +%s)
age=$(( now - ${ts%.*} ))
if [ "$age" -gt 300 ]; then
  echo "$base"
  exit 0
fi

# Build catch message
if [ "$is_new" = "true" ]; then
  msg="new egg!"
elif [ "$hatched" = "true" ]; then
  msg="hatched!"
elif [ "$evolved" = "true" ]; then
  msg="evolved!"
else
  msg="lv.${level}"
fi

echo "$base | ♦ ${word} — ${msg} (${count} caught)"
