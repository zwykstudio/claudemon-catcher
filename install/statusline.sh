#!/bin/bash
#
# Claudemon statusline for Claude Code
#
# Claude Code calls this once per assistant message. Since the engine needs
# a few seconds to sync, we briefly poll for the result before returning.
#
# Install:
#   cc --install-statusline
#

STATUSLINE_DIR="$HOME/.claudemon"
HEALTH_FILE="$STATUSLINE_DIR/engine.status"
DEBUG_LOG="$STATUSLINE_DIR/statusline-debug.log"

# Read Claude Code session data from stdin
input=$(cat)

# Base info from Claude Code
MODEL=$(echo "$input" | jq -r '.model.display_name // empty' 2>/dev/null)
PCT=$(echo "$input" | jq -r '.context_window.used_percentage // 0' 2>/dev/null | cut -d. -f1)

base=""
[ -n "$MODEL" ] && base="$MODEL"
[ -n "$PCT" ] && [ "$PCT" != "0" ] && base="$base ${PCT}%"

TAG="[Claudemon]"

# Debug logging (enabled by CLAUDEMON_DEBUG=1)
_sl_debug() {
  [ "${CLAUDEMON_DEBUG:-}" = "1" ] && printf '%s %s\n' "$(date +%H:%M:%S)" "$*" >> "$DEBUG_LOG" 2>/dev/null
}

# Colors (ANSI-C quoting)
RST=$'\033[0m'
RED=$'\033[31m'
GREEN=$'\033[32m'
YELLOW=$'\033[33m'
MAGENTA=$'\033[35m'
DIM=$'\033[2m'

# Not running through the wrapper → plain statusline
SID="${CLAUDEMON_SID:-}"
if [ -z "$SID" ]; then
  echo "$base"
  exit 0
fi

# Check engine health
if [ -f "$HEALTH_FILE" ]; then
  health=$(cat "$HEALTH_FILE" 2>/dev/null)
  h_status=$(echo "$health" | jq -r '.status // "ok"')
  h_error=$(echo "$health" | jq -r '.error // empty')
  h_ts=$(echo "$health" | jq -r '.ts // 0')
  h_age=$(( $(date +%s) - ${h_ts%.*} ))

  if [ "$h_status" = "error" ] && [ "$h_age" -lt 300 ]; then
    short_err="${h_error:0:50}"
    printf '%s | %s %s⚠ %s%s\n' "$base" "$TAG" "$RED" "$short_err" "$RST"
    exit 0
  fi
fi

ENGINE_FILE="$STATUSLINE_DIR/statusline-$SID.json"
LIVE_FILE="$STATUSLINE_DIR/statusline-$SID-live.json"

# --- Read live file (written by wrapper) ---
_read_live() {
  live_word="" ; live_ts=0
  if [ -f "$LIVE_FILE" ]; then
    local d
    d=$(cat "$LIVE_FILE" 2>/dev/null)
    if [ -n "$d" ]; then
      eval "$(echo "$d" | jq -r '
        @sh "live_word=\(.word // "")",
        @sh "live_ts=\(.ts // 0)"
      ' 2>/dev/null | tr ',' '\n')"
    fi
  fi
}

# --- Read engine file (written by engine) ---
_read_engine() {
  eng_word="" ; eng_event="" ; eng_xp=0 ; eng_dur=0
  eng_count=0 ; eng_total_xp=0 ; eng_ts=0
  if [ -f "$ENGINE_FILE" ]; then
    local d
    d=$(cat "$ENGINE_FILE" 2>/dev/null)
    if [ -n "$d" ]; then
      eval "$(echo "$d" | jq -r '
        @sh "eng_word=\(.word // "")",
        @sh "eng_event=\(.event // "")",
        @sh "eng_xp=\(.xp // 0)",
        @sh "eng_dur=\(.duration // 0)",
        @sh "eng_count=\(.count // 0)",
        @sh "eng_total_xp=\(.total_xp // 0)",
        @sh "eng_ts=\(.ts // 0)"
      ' 2>/dev/null | tr ',' '\n')"
    fi
  fi
}

# Helper: check if engine already synced the live word (float-safe comparison)
engine_has_synced_live() {
  [ -n "$eng_word" ] && [ "$eng_word" = "$live_word" ] && \
    awk "BEGIN{exit!($eng_ts >= $live_ts)}" 2>/dev/null
}

_read_live
_read_engine

_sl_debug "SID=$SID live=$live_word:$live_ts eng=$eng_word:$eng_ts"

# --- If there's a pending capture, wait for engine to sync (up to ~8s) ---
now=$(date +%s)
if [ -n "$live_word" ] && [ $(( now - ${live_ts%.*} )) -lt 120 ] && ! engine_has_synced_live; then
  _sl_debug "waiting for engine to sync '$live_word'..."
  for _i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16; do
    sleep 0.5
    _read_engine
    if engine_has_synced_live; then
      _sl_debug "→ synced after $((_i))x0.5s"
      break
    fi
  done
fi

# --- Build summary ---
summary=""
[ "$eng_count" -gt 0 ] 2>/dev/null && summary="${eng_count} caught"
[ "$eng_total_xp" -gt 0 ] 2>/dev/null && summary="${eng_count} caught · ${eng_total_xp}xp"

now=$(date +%s)

# --- Show synced result if engine has data ---
if [ -n "$eng_word" ] && [ $(( now - ${eng_ts%.*} )) -lt 300 ]; then
  line="$eng_word"
  [ "$eng_xp" -gt 0 ] 2>/dev/null && line="$line ${DIM}+${eng_xp}xp${RST}"
  [ "$eng_dur" != "0" ] && [ -n "$eng_dur" ] && line="$line ${DIM}${eng_dur}s${RST}"
  case "$eng_event" in
    new)      line="$line ${GREEN}NEW${RST}" ;;
    evolved)  line="$line ${MAGENTA}EVOLVED${RST}" ;;
    hatched)  line="$line ${YELLOW}HATCHED${RST}" ;;
  esac
  [ -z "$summary" ] && summary="${eng_count} caught"
  _sl_debug "→ synced ${eng_word} +${eng_xp}xp ${eng_event}"
  printf '%s | %s %s (%s)\n' "$base" "$TAG" "$line" "$summary"
  exit 0
fi

# --- Fallback: capturing (engine didn't sync in time) ---
if [ -n "$live_word" ] && [ $(( now - ${live_ts%.*} )) -lt 120 ]; then
  _sl_debug "→ capturing ${live_word} (engine timeout)"
  line="${DIM}${live_word}...${RST}"
  if [ -n "$summary" ]; then
    printf '%s | %s %s (%s)\n' "$base" "$TAG" "$line" "$summary"
  else
    printf '%s | %s %s\n' "$base" "$TAG" "$line"
  fi
  exit 0
fi

# --- No data or stale ---
if [ -n "$summary" ]; then
  _sl_debug "→ summary only"
  printf '%s | %s (%s)\n' "$base" "$TAG" "$summary"
else
  _sl_debug "→ tag only"
  echo "$base | $TAG"
fi
