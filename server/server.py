#!/usr/bin/env python3
"""
server.py - Web server for Claudemon collection

Serves the API and static frontend (Next.js export).

Usage:
    ./server.py [port]         Start the server (default: 8888)
    ./server.py --update       Update frontend from GitHub releases
    ./server.py --check-update Check if an update is available
"""

import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.database import (
    get_all_claudemons, get_stats, get_team, get_eggs, get_hatched,
    add_to_team, remove_from_team, get_claudemon,
    add_notification, get_notifications, get_all_notifications
)
BASE_DIR = Path(__file__).parent.parent  # claudemon/
STATIC_DIR = BASE_DIR / "web-app" / "out"  # Next.js static export (legacy)
CREATURES_DIR = BASE_DIR / "creatures"
VERSION_FILE = BASE_DIR / ".frontend-version"

# GitHub release info
GITHUB_REPO = "zwykstudio/claudemon"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


class ClaudemonHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Quiet logging
        pass

    def send_json(self, data, status=200):
        content = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(content))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(content)

    def send_file_content(self, content: bytes, content_type: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(content))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(content)

    def send_static_file(self, path: Path):
        if not path.exists() or not path.is_file():
            return False

        content = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"

        # Special handling for HTML
        if path.suffix == ".html":
            content_type = "text/html; charset=utf-8"

        self.send_file_content(content, content_type)
        return True

    def send_image(self, data, content_type="image/png"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(data))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # === API ENDPOINTS ===

        if path == "/api/claudemons":
            claudemons = get_all_claudemons()
            stats = get_stats()
            self.send_json({
                "claudemons": claudemons,
                "stats": stats,
            })
            return

        if path == "/api/stats":
            self.send_json(get_stats())
            return

        if path == "/api/team":
            self.send_json({"team": get_team()})
            return

        if path == "/api/eggs":
            self.send_json({"eggs": get_eggs()})
            return

        if path == "/api/hatched":
            self.send_json({"hatched": get_hatched()})
            return

        if path.startswith("/api/claudemon/"):
            word = path[15:].rstrip("/")
            claudemon = get_claudemon(word)
            if claudemon:
                self.send_json(claudemon)
            else:
                self.send_json({"error": "Not found"}, 404)
            return

        if path.startswith("/api/creature/"):
            # Serve plaintext creature images from creatures/ dir
            parts = path[14:].rstrip("/").split("/")
            if len(parts) == 2:
                word, level_str = parts
                try:
                    level = int(level_str)
                    img_path = CREATURES_DIR / f"{word}-lvl{level}.png"
                    if img_path.exists():
                        self.send_image(img_path.read_bytes())
                        return
                except ValueError:
                    pass
            self.send_error(404)
            return

        # Get recent notifications (for polling)
        if path == "/api/notifications":
            since = 0
            query = parsed.query
            if query:
                for param in query.split("&"):
                    if param.startswith("since="):
                        try:
                            since = float(param[6:])
                        except ValueError:
                            pass
            self.send_json({"notifications": get_notifications(since)})
            return

        # Get all notifications (history)
        if path == "/api/notifications/all":
            self.send_json({"notifications": get_all_notifications()})
            return

        if path.startswith("/api/"):
            self.send_json({"error": "Not found"}, 404)
            return

        # === STATIC FILES (Next.js export) ===

        if not STATIC_DIR.exists():
            self.send_response(503)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <html><body style="font-family: monospace; padding: 2em;">
                <h1>Frontend not installed</h1>
                <p>Run: <code>python server.py --update</code></p>
                </body></html>
            """)
            return

        # Normalize path
        if path == "/":
            path = "/index.html"
        elif path.endswith("/"):
            path = path + "index.html"

        # Security: resolve and check path
        try:
            file_path = (STATIC_DIR / path.lstrip("/")).resolve()
            if not str(file_path).startswith(str(STATIC_DIR.resolve())):
                self.send_error(403)
                return
        except Exception:
            self.send_error(400)
            return

        # Try exact path
        if self.send_static_file(file_path):
            return

        # Try with .html extension (for /collection -> /collection.html)
        if not file_path.suffix:
            html_path = file_path.with_suffix(".html")
            if self.send_static_file(html_path):
                return

        # Try as directory with index.html
        index_path = file_path / "index.html"
        if self.send_static_file(index_path):
            return

        # SPA fallback: serve index.html for client-side routing
        fallback = STATIC_DIR / "index.html"
        if fallback.exists():
            self.send_static_file(fallback)
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode() if content_length else ""

        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid JSON"}, 400)
            return

        if path == "/api/team/add":
            word = data.get("word")
            if not word:
                self.send_json({"error": "Missing word"}, 400)
                return
            success, message = add_to_team(word)
            self.send_json({"success": success, "message": message})
            return

        if path == "/api/team/remove":
            word = data.get("word")
            if not word:
                self.send_json({"error": "Missing word"}, 400)
                return
            success = remove_from_team(word)
            self.send_json({"success": success})
            return

        if path == "/api/notify":
            # Store notification in database
            add_notification(
                notif_type=data.get("type", "info"),
                title=data.get("title", "Claudemon"),
                message=data.get("message", ""),
                word=data.get("word"),
                level=data.get("level"),
                native_sent=data.get("native_sent", False)
            )
            self.send_json({"success": True})
            return

        self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_HEAD(self):
        """Handle HEAD requests (used by Next.js for prefetching)."""
        parsed = urlparse(self.path)
        path = parsed.path

        # For static files, check if they exist
        if not path.startswith("/api/"):
            if path == "/":
                path = "/index.html"
            elif path.endswith("/"):
                path = path + "index.html"

            try:
                file_path = (STATIC_DIR / path.lstrip("/")).resolve()
                if str(file_path).startswith(str(STATIC_DIR.resolve())):
                    if file_path.exists() and file_path.is_file():
                        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
                        self.send_response(200)
                        self.send_header("Content-Type", content_type)
                        self.send_header("Content-Length", file_path.stat().st_size)
                        self.end_headers()
                        return
                    # Try with .html extension
                    html_path = file_path.with_suffix(".html")
                    if html_path.exists():
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Length", html_path.stat().st_size)
                        self.end_headers()
                        return
            except Exception:
                pass

        # Default: return 200 for any path (prefetch hint)
        self.send_response(200)
        self.end_headers()


def get_current_version() -> str | None:
    """Get currently installed frontend version."""
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return None


def get_latest_release() -> dict | None:
    """Fetch latest release info from GitHub using gh CLI."""
    try:
        # Use gh CLI which handles authentication for private repos
        result = subprocess.run(
            ["gh", "release", "view", "--repo", GITHUB_REPO, "--json", "tagName,assets"],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            # Fallback to API for public repos
            try:
                req = urllib.request.Request(GITHUB_API)
                req.add_header("Accept", "application/vnd.github.v3+json")
                req.add_header("User-Agent", "claudemon-updater")
                with urllib.request.urlopen(req, timeout=10) as response:
                    return json.loads(response.read().decode())
            except Exception as e:
                print(f"Error fetching release info: {e}")
                return None

        data = json.loads(result.stdout)
        # Convert gh CLI format to match API format
        return {
            "tag_name": data.get("tagName"),
            "assets": [
                {
                    "name": asset.get("name"),
                    "browser_download_url": asset.get("url")
                }
                for asset in data.get("assets", [])
            ]
        }
    except FileNotFoundError:
        print("Error: gh CLI not installed. Install from https://cli.github.com/")
        return None
    except Exception as e:
        print(f"Error fetching release info: {e}")
        return None


def download_and_extract(url: str, dest: Path, version: str = None) -> bool:
    """Download tarball and extract to destination."""
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / "frontend.tar.gz"

            # Try gh CLI first (works for private repos)
            if version:
                print(f"Downloading frontend.tar.gz...")
                result = subprocess.run(
                    ["gh", "release", "download", version,
                     "--repo", GITHUB_REPO,
                     "--pattern", "frontend.tar.gz",
                     "--dir", tmp_dir],
                    capture_output=True,
                    text=True,
                    timeout=120
                )

                if result.returncode != 0:
                    # Fallback to URL download
                    print(f"gh download failed, trying URL: {url}")
                    req = urllib.request.Request(url)
                    req.add_header("User-Agent", "claudemon-updater")
                    with open(tmp_path, "wb") as f:
                        with urllib.request.urlopen(req, timeout=60) as response:
                            shutil.copyfileobj(response, f)
            else:
                # Direct URL download
                print(f"Downloading {url}...")
                req = urllib.request.Request(url)
                req.add_header("User-Agent", "claudemon-updater")
                with open(tmp_path, "wb") as f:
                    with urllib.request.urlopen(req, timeout=60) as response:
                        shutil.copyfileobj(response, f)

            print("Extracting...")

            # Remove old static dir
            if dest.exists():
                shutil.rmtree(dest)
            dest.mkdir(parents=True)

            # Extract
            with tarfile.open(tmp_path, "r:gz") as tar:
                tar.extractall(dest)

        return True
    except Exception as e:
        print(f"Error downloading/extracting: {e}")
        return False


def check_update():
    """Check if an update is available."""
    current = get_current_version()
    release = get_latest_release()

    if not release:
        print("Could not fetch release info")
        return False

    latest = release.get("tag_name", "unknown")

    print(f"Current version: {current or 'not installed'}")
    print(f"Latest version:  {latest}")

    if current == latest:
        print("Already up to date")
        return False

    print("Update available")
    return True


def update_frontend():
    """Download and install latest frontend release."""
    print("Checking for updates...")

    release = get_latest_release()
    if not release:
        print("Error: Could not fetch release info")
        return False

    version = release.get("tag_name", "unknown")
    current = get_current_version()

    if current == version:
        print(f"Already at latest version ({version})")
        return True

    # Find the frontend asset (frontend.tar.gz)
    assets = release.get("assets", [])
    frontend_asset = None

    for asset in assets:
        if asset.get("name") == "frontend.tar.gz":
            frontend_asset = asset
            break

    if not frontend_asset:
        print("Error: No frontend.tar.gz in release")
        print("Available assets:", [a.get("name") for a in assets])
        return False

    download_url = frontend_asset.get("browser_download_url")
    if not download_url:
        print("Error: No download URL")
        return False

    print(f"Updating to {version}...")

    if download_and_extract(download_url, STATIC_DIR, version):
        VERSION_FILE.write_text(version)
        print(f"Updated to {version}")
        return True

    return False


DEFAULT_PORT = 17712


def run_server(port=DEFAULT_PORT):
    server = HTTPServer(("", port), ClaudemonHandler)
    print(f"Claudemon: http://localhost:{port}")

    if not STATIC_DIR.exists():
        print("  Warning: Frontend not installed. Run: python server.py --update")

    print("  Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


def main():
    args = sys.argv[1:]

    if "--update" in args:
        update_frontend()
    elif "--check-update" in args:
        check_update()
    else:
        port = DEFAULT_PORT
        for arg in args:
            try:
                port = int(arg)
                break
            except ValueError:
                continue
        run_server(port)


if __name__ == "__main__":
    main()
