import os
import sys
import asyncio
import tempfile
import subprocess
import time
import re
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ.get("DISCORD_TOKEN")
GUILD_ID = os.environ.get("DISCORD_GUILD_ID")

SPOTIPY_CLIENT_ID = os.environ.get("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.environ.get("SPOTIPY_CLIENT_SECRET")

MAX_FILE_MB = 25
ALLOWED_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}

# Màu đen tối giản
COLOR_MINIMAL = 0x000000

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

def minimal_embed(title: str, description: str = "") -> discord.Embed:
    """Tạo embed tối giản, không viền rườm rà, màu đen"""
    return discord.Embed(title=title, description=description, color=COLOR_MINIMAL)

def fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"

def resolve_spotify_track(url: str) -> Optional[str]:
    if not SPOTIPY_CLIENT_ID or not SPOTIPY_CLIENT_SECRET:
        return None
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        sp = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=SPOTIPY_CLIENT_ID, client_secret=SPOTIPY_CLIENT_SECRET
            )
        )
        match = re.search(r"track[/=]([a-zA-Z0-9]+)", url)
        if match:
            track_id = match.group(1)
            track = sp.track(track_id)
            track_name = track['name']
            artist_name = track['artists'][0]['name']
            return f"{artist_name} - {track_name} audio"
    except Exception:
        pass
    return None

def download_audio_from_link(url: str, output_base_path: str) -> tuple[bool, str, str, str]:
    import yt_dlp
    query_or_url = url
    if "spotify.com" in url:
        search_query = resolve_spotify_track(url)
        if search_query:
            query_or_url = f"ytsearch1:{search_query}"
        else:
            query_or_url = f"ytsearch1:{url}"

ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_base_path,
        'username': 'oauth2',
        'password': '',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'wav',
            'preferredquality': '192',
        }],
        'quiet': True,
        'no_warnings': True,
        'default_search': 'ytsearch',
    }

    if os.path.exists("cookies.txt"):
        ydl_opts['cookiefile'] = "cookies.txt"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query_or_url, download=True)
            
            if not info:
                return False, "Không tìm thấy nội dung âm thanh phù hợp.", "", ""

            if 'entries' in info:
                entries = [e for e in info['entries'] if e is not None]
                if not entries:
                    return False, "Không tìm thấy video nào từ kết quả tìm kiếm.", "", ""
                title = entries[0].get('title', 'Unknown Track')
            else:
                title = info.get('title', 'Unknown Track')

        final_file = output_base_path + ".wav"
        if not os.path.exists(final_file):
            for file_in_dir in os.listdir(os.path.dirname(output_base_path)):
                if file_in_dir.startswith(os.path.basename(output_base_path)):
                    final_file = os.path.join(os.path.dirname(output_base_path), file_in_dir)
                    break

        return True, "", final_file, title
    except Exception as e:
        return False, str(e), "", ""
def run_transkun_cli(input_path: str, output_path: str) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "transkun.transcribe", input_path, output_path, "--device", "cpu"]
    env = os.environ.copy()
    env["TORCHAUDIO_USE_BACKEND_DISPATCHER"] = "0"
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if res.returncode == 0 and os.path.exists(output_path):
        return True, ""
    err_msg = res.stderr.strip() if res.stderr else res.stdout.strip()
    return False, err_msg[-1000:] if err_msg else "Lỗi xử lý MIDI."

def analyze_midi(midi_path: str) -> dict:
    import pretty_midi
    pm = pretty_midi.PrettyMIDI(midi_path)
    notes = [n for inst in pm.instruments for n in inst.notes]
    duration = pm.get_end_time()
    
    stats = {
        "note_count": len(notes),
        "duration": duration,
        "tempo": 0.0,
    }
    
    if notes:
        try:
            stats["tempo"] = round(float(pm.estimate_tempo()), 1)
        except Exception:
            stats["tempo"] = 0.0
    return stats

@tree.command(name="transcribe", description="Chuyển đổi âm thanh thành MIDI")
@app_commands.describe(
    file="File âm thanh (mp3, wav...)",
    url="Link (Spotify, YouTube...)"
)
async def transcribe(
    interaction: discord.Interaction, 
    file: Optional[discord.Attachment] = None, 
    url: Optional[str] = None
):
    if not file and not url:
        await interaction.response.send_message(
            embed=minimal_embed("Lỗi", "Vui lòng đính kèm file hoặc URL."),
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    with tempfile.TemporaryDirectory() as tmp:
        input_audio_path = os.path.join(tmp, "input_track")
        midi_output_path = os.path.join(tmp, "output.mid")
        
        # Tiêu đề tạm thời
        display_title = Path(file.filename).stem if file else "Processing URL..."
        
        # Giao diện Đang xử lý (Giống Image 1)
        working_desc = "░░░░░░░░░░░░░░░░░░░░ **0%**\n\nworking"
        await interaction.edit_original_response(embed=minimal_embed(display_title, working_desc))

        if url:
            dl_ok, dl_err, downloaded_file, track_title = await asyncio.to_thread(download_audio_from_link, url, input_audio_path)
            if not dl_ok:
                await interaction.edit_original_response(embed=minimal_embed("Lỗi tải nhạc", f"```\n{dl_err[:500]}\n```"))
                return
            input_audio_path = downloaded_file
            display_title = track_title

        elif file:
            ext = Path(file.filename).suffix.lower()
            if ext not in ALLOWED_EXT or file.size > MAX_FILE_MB * 1024 * 1024:
                await interaction.edit_original_response(embed=minimal_embed("Lỗi định dạng/dung lượng", "File không hợp lệ hoặc quá lớn."))
                return

            input_audio_path = os.path.join(tmp, f"input{ext}")
            await file.save(input_audio_path)

        # Cập nhật lại tên bài nếu là URL
        await interaction.edit_original_response(embed=minimal_embed(display_title, working_desc))

        # Xử lý Transkun
        ok, err = await asyncio.to_thread(run_transkun_cli, input_audio_path, midi_output_path)

        if not ok or not os.path.exists(midi_output_path):
            await interaction.edit_original_response(embed=minimal_embed("Thất bại", f"```\n{err}\n```"))
            return

        # Đọc thông số MIDI
        try:
            stats = await asyncio.to_thread(analyze_midi, midi_output_path)
        except Exception:
            stats = None

        # Giao diện Hoàn thành (Giống Image 2)
        if stats:
            stats_ui = f"` {stats['note_count']} notes ` ` {stats['tempo']} BPM ` ` {fmt_duration(stats['duration'])} `"
        else:
            stats_ui = "` Không thể đọc thông số MIDI `"

        description = (
            f"{stats_ui}\n\n"
            f"Hãy sử dụng lại lệnh `/transcribe` trong <#1545367143359713330> để chuyển đổi MP3 sang MIDI, và chuyển đổi liên kết sang MIDI.\n\n" # Bạn có thể thay ID kênh showcase của bạn vào đây
            f"{interaction.user.mention}"
        )

        finished_embed = minimal_embed(display_title, description)

        # Chuẩn bị file gửi kèm (Khi đính kèm file cùng embed, Discord sẽ tự động nhúng file vào trong khối embed)
        safe_filename = "".join(c for c in display_title if c.isalnum() or c in " _-").strip()
        midi_file = discord.File(midi_output_path, filename=f"{safe_filename}.mid")

        await interaction.edit_original_response(embed=finished_embed, attachments=[midi_file])

@client.event
async def on_ready():
    activity = discord.Activity(type=discord.ActivityType.watching, name="/transcribe")
    await client.change_presence(status=discord.Status.idle, activity=activity)

    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
        print("Synced to Guild.")
    else:
        await tree.sync()
        print("Synced Globally.")

    print(f"Logged in as: {client.user}")

def main():
    if not TOKEN:
        raise SystemExit("Missing DISCORD_TOKEN.")
    client.run(TOKEN)

if __name__ == "__main__":
    main()
