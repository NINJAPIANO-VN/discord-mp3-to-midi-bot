import os
import sys
import asyncio
import tempfile
import subprocess
import time
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ.get("DISCORD_TOKEN")
GUILD_ID = os.environ.get("DISCORD_GUILD_ID")

MAX_FILE_MB = 25
ALLOWED_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}

COLOR_MAIN = 0x0B0B0C
COLOR_OK = 0x0B0B0C
COLOR_ERR = 0x1A0A0A
COLOR_WORKING = 0x0B0B0C

BAR = "─" * 22

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


def base_embed(title: str, description: str = "", color: int = COLOR_MAIN) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    if client.user:
        embed.set_footer(
            text="Transkun V2 · Audio/Link → MIDI",
            icon_url=client.user.display_avatar.url,
        )
    embed.timestamp = discord.utils.utcnow()
    return embed


def fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def download_audio_from_link(url: str, output_base_path: str) -> tuple[bool, str, str]:
    """
    Tải audio từ link (YouTube, SoundCloud,...) bằng yt-dlp và ép xuất ra file .wav.
    """
    import yt_dlp

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_base_path,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'wav',
            'preferredquality': '192',
        }],
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        # Kiểm tra file wav tạo ra
        final_file = output_base_path + ".wav"
        if not os.path.exists(final_file):
            # Fallback nếu yt-dlp giữ nguyên extension gốc
            for file_in_dir in os.listdir(os.path.dirname(output_base_path)):
                if file_in_dir.startswith(os.path.basename(output_base_path)):
                    final_file = os.path.join(os.path.dirname(output_base_path), file_in_dir)
                    break

        return True, "", final_file
    except Exception as e:
        return False, str(e), ""


def run_transkun_cli(input_path: str, output_path: str) -> tuple[bool, str]:
    """
    Gọi trực tiếp CLI transkun thông qua subprocess.
    """
    cmd = [
        sys.executable,
        "-m",
        "transkun.transcribe",
        input_path,
        output_path,
        "--device",
        "cpu",
    ]

    env = os.environ.copy()
    env["TORCHAUDIO_USE_BACKEND_DISPATCHER"] = "0"

    res = subprocess.run(cmd, capture_output=True, text=True, env=env)

    if res.returncode == 0 and os.path.exists(output_path):
        return True, ""

    err_msg = res.stderr.strip() if res.stderr else res.stdout.strip()
    return False, err_msg[-1000:] if err_msg else "Không thể khởi tạo file MIDI output."


def analyze_midi(midi_path: str) -> dict:
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(midi_path)
    notes = [n for inst in pm.instruments for n in inst.notes]
    duration = pm.get_end_time()

    stats = {
        "note_count": len(notes),
        "duration": duration,
        "lowest": "—",
        "highest": "—",
        "avg_velocity": 0,
        "tempo": 0.0,
        "density": 0.0,
    }

    if notes:
        pitches = [n.pitch for n in notes]
        velocities = [n.velocity for n in notes]
        stats["lowest"] = pretty_midi.note_number_to_name(min(pitches))
        stats["highest"] = pretty_midi.note_number_to_name(max(pitches))
        stats["avg_velocity"] = round(sum(velocities) / len(velocities), 1)
        stats["density"] = round(len(notes) / duration, 2) if duration > 0 else 0.0
        try:
            stats["tempo"] = round(float(pm.estimate_tempo()), 1)
        except Exception:
            stats["tempo"] = 0.0

    return stats


@tree.command(name="transcribe", description="Chuyển đổi âm thanh (File hoặc Link) thành file MIDI")
@app_commands.describe(
    file="File âm thanh piano cần chuyển (mp3, wav, m4a, ogg, flac)",
    url="Đường link bài nhạc (YouTube, SoundCloud, v.v.)"
)
async def transcribe(
    interaction: discord.Interaction, 
    file: Optional[discord.Attachment] = None, 
    url: Optional[str] = None
):
    if not file and not url:
        await interaction.response.send_message(
            embed=base_embed("Thiếu thông tin", "Vui lòng tải lên 1 file đính kèm **HOẶC** dán 1 đường link nhạc.", color=COLOR_ERR),
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    with tempfile.TemporaryDirectory() as tmp:
        input_audio_path = os.path.join(tmp, "input_track")
        midi_output_path = os.path.join(tmp, "output.mid")
        source_name = ""

        # Trường hợp 1: Người dùng gửi link
        if url:
            working = base_embed(
                "Đang tải bài nhạc từ link",
                f"{BAR}\nĐang xử lý dữ liệu âm thanh từ link:\n`{url}`",
                color=COLOR_WORKING,
            )
            await interaction.edit_original_response(embed=working)

            dl_ok, dl_err, downloaded_file = await asyncio.to_thread(download_audio_from_link, url, input_audio_path)
            if not dl_ok or not os.path.exists(downloaded_file):
                await interaction.edit_original_response(
                    embed=base_embed("Không thể tải bài nhạc", f"{BAR}\nLỗi khi tải từ link:\n```\n{dl_err[:500]}\n
