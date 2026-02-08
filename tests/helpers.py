"""
tests/conftest.py - Shared helpers and constants for Claudemon tests.

Run:  python3 -m pytest tests/ -v
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

STATUSLINE_SH = os.path.join(ROOT, "install", "statusline.sh")


def _import_wrapper():
    """Import wrapper module (no side-effects on import)."""
    import wrapper
    return wrapper


def _import_database(tmp_path):
    """Import database module with DB_PATH redirected to tmp_path.

    database.py calls init_db() at module level, so we must patch DB_PATH
    *before* the first import, or re-patch and re-init for each test.
    """
    import engine.database as db
    db.DB_PATH = tmp_path / "test.db"
    db.init_db()
    return db


def _run_statusline(tmp_dir, sid, live_data=None, engine_data=None,
                    health_data=None, stdin_json=None):
    """Helper to run statusline.sh with controlled files and env."""
    import subprocess

    sl_dir = str(tmp_dir)
    if live_data is not None:
        (tmp_dir / f"statusline-{sid}-live.json").write_text(json.dumps(live_data))
    if engine_data is not None:
        (tmp_dir / f"statusline-{sid}.json").write_text(json.dumps(engine_data))
    if health_data is not None:
        (tmp_dir / "engine.status").write_text(json.dumps(health_data))

    env = os.environ.copy()
    env["CLAUDEMON_SID"] = sid
    env["HOME"] = str(tmp_dir.parent)  # so ~/.claudemon resolves to tmp_dir

    # Create the ~/.claudemon symlink target
    home_claudemon = tmp_dir.parent / ".claudemon"
    if not home_claudemon.exists():
        home_claudemon.symlink_to(tmp_dir)

    stdin_data = json.dumps(stdin_json or {"model": {"display_name": "Opus"}})

    result = subprocess.run(
        ["bash", STATUSLINE_SH],
        input=stdin_data, capture_output=True, text=True,
        env=env, timeout=15,
    )
    return result.stdout.strip()
