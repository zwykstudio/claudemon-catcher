# Changelog

All notable changes to Claudemon Catcher are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/). Versions use [Semantic Versioning](https://semver.org/).

## [0.2.11] - 2026-02-24

### Fixed
- Word detection now captures slang/colloquial spinner words ending in `in'` (e.g. "Beboppin'") instead of only `ing`

## [0.2.10] - 2026-02-14

### Improved
- Session recap now displays header instantly on exit instead of waiting silently for engine sync
- Shows "syncing catches…" indicator when engine data isn't ready yet (previously: blank screen for up to 1.2s)

## [0.2.9] - 2026-02-11

### Added
- Auto-update on launch — when a new version is detected, `git pull` + dependency install runs automatically before launching Claude
- `certifi` added as a project dependency for SSL certificate verification
- `setup.sh` and `cc update` now install missing Python dependencies (`certifi`, `cryptography`)

### Fixed
- SSL `CERTIFICATE_VERIFY_FAILED` on macOS with python.org/pyenv installs — uses `certifi` CA bundle

## [0.2.8] - 2026-02-11

### Fixed
- SSL `CERTIFICATE_VERIFY_FAILED` on macOS with python.org/pyenv installs — now uses `certifi` CA bundle as fallback
- Added `certifi` as a dependency

## [0.2.7] - 2026-02-10

### Added
- Auto-bypass when custom `spinnerVerbs` detected in Claude settings — wrapper disables PTY capture gracefully and shows a warning
- Note in README about spinnerVerbs compatibility

## [0.2.6] - 2026-02-10

### Fixed
- **Security**: PowerShell notification injection — user strings no longer interpolated in PS code (uses `-EncodedCommand` + env vars)
- **Security**: PlistBuddy command injection — API key now passed via `defaults write` with strict alphanumeric validation
- **Security**: `.claude/` added to `.gitignore` to prevent accidental commit of API keys
- SQLite `DeprecationWarning` on Python 3.12+ — dates stored as ISO strings instead of relying on deprecated implicit adapters

### Added
- Ruff linter with `pyproject.toml` configuration (`E`, `F`, `W`, `I` rules)
- `pyproject.toml` with project metadata and dev dependencies
- 10 new tests covering security fixes (PowerShell injection, key validation, PlistBuddy)

### Changed
- Cleaned up 35 lint issues (unused imports, dead variables, f-strings without placeholders, unsorted imports)

## [0.2.5] - 2026-02-10

### Fixed
- Corrupted word captures (e.g. "Dtermiing", "Bfuddling", "Propaging") caused by Ink's differential rendering using ANSI cursor positioning (`\x1b[nG`) — characters from different screen columns were concatenated into fake words
- DEC private sequences (`\x1b[?2026h`, `\x1b[?25l`) no longer accumulate as noise in the buffer

## [0.2.4] - 2026-02-09

### Fixed
- Truncated word captures (e.g. "Propaging" instead of "Propagating") caused by spinner `\r` rewrites accumulating in the buffer

## [0.2.3] - 2026-02-09

### Fixed
- Update check no longer false-flags when local is ahead of remote (unpushed commits)

## [0.2.2] - 2026-02-09

### Added
- Version update check at startup: compares local HEAD with remote, shows "up to date" or "update available" inline on the banner
- `cc update` command: pulls latest code and restarts the engine daemon in one step
- 24h cache (`~/.claudemon/version.check`) to avoid hitting git remote on every launch

## [0.2.1] - 2026-02-09

### Fixed
- CLI dispatch intercepted flags anywhere in argv (e.g. `cc -p "--help"` was caught by claudemon instead of passed to Claude) — now only checks argv[1]
- Recap column spacing: XP and duration could merge when duration >= 100s (e.g. `+30xp411.9s`)
- Statusline showed "capturing..." even when engine had already synced the word

## [0.2.0] - 2026-02-08

### Added
- Statusline polling: script waits up to 8s for engine sync instead of returning stale data
- Periodic live file refresh during long captures (every ~5s) to prevent staleness
- Engine health reporting in statusline (shows errors from `engine.status`)
- Debug logging for statusline (`CLAUDEMON_DEBUG=1`)
- Session recap on close (`print_recap()`)
- Notification pooling in engine
- API key validity check on startup

### Changed
- Split test suite from single 1687-line file into 8 focused modules (106 tests)
- Statusline rewritten: read live/engine files, poll for sync, fallback gracefully
- Recap output left-aligned (removed indentation)

### Fixed
- Statusline desync on long captures (>60s) due to stale live file timestamps
- Statusline stuck showing old data when Claude Code only polls once per response

## [0.1.0] - 2026-01-28

### Added
- Initial release: wrapper, engine daemon, CLI
- Word detection from Claude Code output via regex
- Engine watches `catches.jsonl`, syncs to platform via API
- SQLite local database with game logic (XP, levels, evolution)
- CloudStorage sync with retry and exponential backoff
- Statusline integration for Claude Code
- Cross-platform setup (macOS, Linux, Windows)
