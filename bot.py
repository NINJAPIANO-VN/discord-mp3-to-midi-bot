import os
import sys
import asyncio
import tempfile
import subprocess
import time
import re
import urllib.request
import json
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
TIKHUB_API_TOKEN = os.environ.get("TIKHUB_API_TOKEN") # Token đăng ký tại https://user.tikhub.io/

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

def download_tiktok_via_tikhub(url: str, output_base_path: str) -> tuple[bool, str, str, str]:
    """Tải âm thanh TikTok/Douyin thông qua TikHub API"""
    if not TIKHUB_API_TOKEN:
        return False, "Thiếu TIKHUB_API_TOKEN trong cấu hình môi trường.", "", ""

    api_endpoint = f"https://api.tikhub.io/api/v1/tiktok/web/fetch_post_detail?url={urllib.parse.quote(url)}"
    req = urllib.request.Request(api_endpoint)
    req.add_header("Authorization", f"Bearer {TIKHUB_API_TOKEN}")

    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode())

        if res_data.get("code") != 0 and res_data.get("status_code") != 0:
            msg = res_data.get("msg") or res_data.get("message") or "Lỗi từ TikHub API"
            return False, f"TikHub API Error: {msg}", "", ""

        data = res_data.get("data", {})
        
        # Trích xuất tiêu đề bài viết/video
        title = data.get("desc") or "TikTok Audio"
        title = title[:50]  # Giới hạn độ dài tiêu đề

        # Tìm URL nhạc (Audio)
        music_info = data.get("music", {}) or data.get("music_info", {})
        audio_url = music_info.get("play_url", {}).get("url_list", [None])[0] or music_info.get("play_url")

        if not audio_url:
            return False, "Không tìm thấy đường dẫn âm thanh trong video TikTok này.", "", ""

        # Tải file âm thanh gốc về máy
        temp_audio_path = output_base_path + "_raw"
        urllib.request.urlretrieve(audio_url, temp_audio_path)

        # Chuyển đổi file âm thanh sang chuẩn WAV thông qua FFmpeg
        final_wav = output_base_path + ".wav"
        ffmpeg_cmd = ["ffmpeg", "-y", "-i", temp_audio_path, "-vn", "-ar", "44100", "-ac", "2", final_wav]
        subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        if os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)

        return True, "", final_wav, title

    except Exception as e:
        return False, f"Lỗi tải TikTok: {str(e)}", "", ""

def download_audio_from_link(url: str, output_base_path: str) -> tuple[bool, str, str, str]:
    # Nếu là link TikTok/Douyin -> Xử lý bằng TikHub API
    if "tiktok.com" in url or "douyin.com" in url:
        return download_tiktok_via_tikhub(url, output_base_path)

    import yt_dlp
    query_or_url = url
    if "spotify.com" in url:
        search_query = resolve_spotify_track(url)
        if search_query:
            query_or_url = f"ytsearch1:{search_query}"
        else:
            query_or_url = f"ytsearch1:{url}"

    # Cấu hình yt-dlp với client tv_embedded
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_base_path,
        'extractor_args': {
            'youtube': {
                'player_client': ['tv_embedded']
            }
        },
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'wav',
            'preferredquality': '192',
        }],
        'quiet': True,
        'no_warnings': True,
        'default_search': 'ytsearch',
    }

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

@tree.command(name="transcribe", description="Chuyển đổi âm thanh (File/YouTube/TikTok) thành MIDI")
@app_commands.describe(
    file="File âm thanh tùy chọn (mp3, wav...)",
    url="Đường dẫn tùy chọn (TikTok, YouTube, Spotify...)"
)
async def transcribe(
    interaction: discord.Interaction, 
    file: Optional[discord.Attachment] = None, 
    url: Optional[str] = None
):
    if not file and not url:
        await interaction.response.send_message(
            embed=minimal_embed("Lỗi", "Vui lòng tải lên 1 file âm thanh hoặc điền 1 URL."),
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    with tempfile.TemporaryDirectory() as tmp:
        input_audio_path = os.path.join(tmp, "input_track")
        midi_output_path = os.path.join(tmp, "output.mid")
        
        display_title = Path(file.filename).stem if file else "Processing URL..."
        
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
                await interaction.edit_original_response(embed=minimal_embed("Lỗi định dạng/dung lượng", "File không hợp lệ hoặc vượt quá 25MB."))
                return

            input_audio_path = os.path.join(tmp, f"input{ext}")
            await file.save(input_audio_path)

        await interaction.edit_original_response(embed=minimal_embed(display_title, working_desc))

        # Xử lý Transkun
        ok, err = await asyncio.to_thread(run_transkun_cli, input_audio_path, midi_output_path)

        if not ok or not os.path.exists(midi_output_path):
            await interaction.edit_original_response(embed=minimal_embed("Thất bại", f"```\n{err}\n```"))
            return

        try:
            stats = await asyncio.to_thread(analyze_midi, midi_output_path)
        except Exception:
            stats = None

        if stats:
            stats_ui = f"` {stats['note_count']} notes ` ` {stats['tempo']} BPM ` ` {fmt_duration(stats['duration'])} `"
        else:
            stats_ui = "` Không thể đọc thông số MIDI `"

        description = (
            f"{stats_ui}\n\n"
            f"Hãy sử dụng lại lệnh `/transcribe` để chuyển đổi MP3/TikTok/YouTube sang MIDI.\n\n"
            f"{interaction.user.mention}"
        )

        finished_embed = minimal_embed(display_title, description)

        safe_filename = "".join(c for c in display_title if c.isalnum() or c in " _-").strip() or "output"
        midi_file = discord.File(midi_output_path, filename=f"{safe_filename}.mid")

        await interaction.edit_original_response(embed=finished_embed, attachments=[midi_file])

@client.event
async def on_ready():
    activity = discord.Activity(type=discord.ActivityType.listening, name="/transcribe ㆍ NINJAPIANO IS THE BEST")
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
