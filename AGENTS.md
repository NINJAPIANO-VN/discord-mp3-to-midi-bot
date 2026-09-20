# Base44 Setup Notes

## Project Overview
This is a **Discord bot** (not a web app) that transcribes audio to MIDI via the `/transcribe` slash command. It uses:
- **discord.py** for the Discord client
- **transkun** (PyTorch-based) for audio-to-MIDI transcription
- **yt-dlp** for YouTube audio download
- **TikHub API** for TikTok audio download
- **spotipy** for Spotify track resolution
- **FFmpeg** for audio conversion

## Architecture
Since this is a bot with no web UI, a minimal status server (`status_server.py`) runs alongside the bot on port 3000 to provide a visible status page in the preview. The `entrypoint.sh` script starts the bot (with auto-restart) in the background and the status server in the foreground.

## Required Secrets
- `DISCORD_TOKEN` (required) — bot token from Discord Developer Portal
- `DISCORD_GUILD_ID` (optional) — server ID for instant slash command sync
- `SPOTIPY_CLIENT_ID` / `SPOTIPY_CLIENT_SECRET` (optional) — for Spotify URL resolution
- `TIKHUB_API_TOKEN` (optional) — for TikTok audio download

## Dev Environment
- Docker compose: `docker-compose.base44.yml`
- Build: `Dockerfile.base44` (python:3.11-slim + ffmpeg, no torch — keeps image small)
- Source is bind-mounted at `/app` — edits appear on container restart
- The bot auto-restarts if it crashes (see entrypoint.sh)
- Status page: http://localhost:3000
- Health endpoint: http://localhost:3000/health
- Restart after editing bot.py: `docker compose -f docker-compose.base44.yml restart bot`

## Notes
- torch is NOT installed (it adds ~2GB, exceeding Docker storage). The bot connects to Discord and handles audio downloads; MIDI transcription (transkun) needs torch and will fail until it's added.
- The bot has no live-reload; restart the service after editing bot.py
- The status server checks /proc to determine if the bot process is alive
