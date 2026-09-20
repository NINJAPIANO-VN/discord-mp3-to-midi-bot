"""Minimal status page for the Discord Transkun Bot (no web UI exists)."""
import os
import http.server
import socketserver
import json


def is_bot_running():
    """Check if the bot.py process is alive by scanning /proc."""
    try:
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            try:
                with open(f"/proc/{pid_dir}/cmdline", "r") as f:
                    cmdline = f.read()
                    if "bot.py" in cmdline and "status_server" not in cmdline:
                        return True
            except (IOError, PermissionError):
                continue
    except Exception:
        pass
    return False


def get_features():
    """Check which optional integrations are configured (presence only, no values)."""
    return {
        "Discord": bool(os.environ.get("DISCORD_TOKEN")),
        "Spotify": bool(os.environ.get("SPOTIPY_CLIENT_ID") and os.environ.get("SPOTIPY_CLIENT_SECRET")),
        "TikTok": bool(os.environ.get("TIKHUB_API_TOKEN")),
    }


def render_page():
    bot_running = is_bot_running()
    features = get_features()

    status_color = "#4ade80" if bot_running else "#f87171"
    status_text = "Online" if bot_running else "Offline"

    feature_items = []
    for name, configured in features.items():
        icon = "✓" if configured else "✗"
        color = "#4ade80" if configured else "#6b7280"
        feature_items.append(f'<span style="color:{color}">{icon}</span> {name}')
    features_html = " &nbsp; ".join(feature_items)

    bot_note = (
        "✅ Bot is connected and ready — use /transcribe in your Discord server."
        if bot_running
        else "⚠️ Bot is not running — set a valid DISCORD_TOKEN to connect."
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Discord Transkun Bot</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  background:#0a0a0a; color:#e5e5e5; min-height:100vh;
  display:flex; align-items:center; justify-content:center;
}}
.container {{ text-align:center; padding:2rem; max-width:560px; }}
.icon {{
  width:80px; height:80px; margin:0 auto 1.5rem;
  background:#1a1a1a; border-radius:20px;
  display:flex; align-items:center; justify-content:center; font-size:36px;
}}
h1 {{ font-size:1.75rem; font-weight:700; margin-bottom:0.5rem; }}
.subtitle {{ color:#999; margin-bottom:2rem; }}
.status {{
  display:inline-flex; align-items:center; gap:0.5rem;
  padding:0.5rem 1.25rem; background:#1a1a1a; border-radius:999px;
  margin-bottom:1.5rem; font-size:0.95rem;
}}
.dot {{ width:8px; height:8px; border-radius:50%; background:{status_color}; }}
.features {{
  display:flex; flex-wrap:wrap; gap:1rem; justify-content:center;
  margin-bottom:2rem; font-size:0.9rem;
}}
.command {{
  background:#1a1a1a; border:1px solid #333; border-radius:8px;
  padding:1rem; margin-bottom:1.5rem;
  font-family:'SF Mono',Monaco,monospace; font-size:0.9rem;
}}
.command code {{ color:#7c9eff; }}
.info {{ color:#666; font-size:0.85rem; margin-top:1.5rem; line-height:1.6; }}
</style>
</head>
<body>
<div class="container">
  <div class="icon">🎵</div>
  <h1>Discord Transkun Bot</h1>
  <p class="subtitle">Audio-to-MIDI transcription bot</p>
  <div class="status">
    <span class="dot"></span>
    <span style="color:{status_color}">{status_text}</span>
  </div>
  <div class="features">{features_html}</div>
  <div class="command">
    <code>/transcribe</code> — Convert audio (file / YouTube / TikTok / Spotify) to MIDI
  </div>
  <p class="info">{bot_note}</p>
</div>
</body>
</html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "bot_running": is_bot_running()}).encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(render_page().encode())

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("STATUS_PORT", 3000))
    with socketserver.TCPServer(("0.0.0.0", port), Handler) as httpd:
        print(f"Status server running on port {port}")
        httpd.serve_forever()
