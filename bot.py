import os
import sys
import asyncio
import tempfile
import subprocess
import time
from pathlib import Path

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


def download_audio_from_link(url: str, output_path: str) -> tuple[bool, str]:
    """
    Tải audio từ link (YouTube, SoundCloud,...) bằng yt-dlp và chuyển thành MP3/WAV.
    """
    import yt_dlp

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_path,
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
        return True, ""
    except Exception as e:
        return False, str(e)


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
    file: discord.Attachment = None, 
    url: str = None
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

            dl_ok, dl_err = await asyncio.to_thread(download_audio_from_link, url, input_audio_path)
            if not dl_ok:
                await interaction.edit_original_response(
                    embed=base_embed("Không thể tải bài nhạc", f"{BAR}\nLỗi khi tải từ link:\n```\n{dl_err[:500]}\n```", color=COLOR_ERR)
                )
                return
            
            # yt-dlp tự động thêm đuôi .wav
            input_audio_path = input_audio_path + ".wav"
            source_name = "URL Link"

        # Trường hợp 2: Người dùng gửi file đính kèm
        elif file:
            ext = Path(file.filename).suffix.lower()
            if ext not in ALLOWED_EXT:
                await interaction.edit_original_response(
                    embed=base_embed("Định dạng không hỗ trợ", f"Chỉ chấp nhận: `{', '.join(sorted(ALLOWED_EXT))}`", color=COLOR_ERR)
                )
                return

            if file.size > MAX_FILE_MB * 1024 * 1024:
                await interaction.edit_original_response(
                    embed=base_embed("File quá lớn", f"Giới hạn hiện tại: **{MAX_FILE_MB} MB**.", color=COLOR_ERR)
                )
                return

            input_audio_path = os.path.join(tmp, f"input{ext}")
            audio_bytes = await file.read()
            with open(input_audio_path, "wb") as f:
                f.write(audio_bytes)
            source_name = file.filename

        # Bắt đầu quá trình Transcribe sang MIDI
        working = base_embed(
            "Đang tạo MIDI",
            f"{BAR}\nĐang trích xuất nốt nhạc bằng Transkun V2...\nQuá trình này có thể mất vài phút.",
            color=COLOR_WORKING,
        )
        await interaction.edit_original_response(embed=working)

        start = time.time()
        ok, err = await asyncio.to_thread(run_transkun_cli, input_audio_path, midi_output_path)
        elapsed = time.time() - start

        if not ok or not os.path.exists(midi_output_path):
            await interaction.edit_original_response(
                embed=base_embed("Chuyển đổi thất bại", f"{BAR}\n```\n{err}\n```", color=COLOR_ERR)
            )
            return

        try:
            stats = await asyncio.to_thread(analyze_midi, midi_output_path)
        except Exception as e:
            stats = None
            analyze_error = str(e)

        out_name = (Path(source_name).stem if source_name != "URL Link" else "converted_track") + ".mid"
        midi_file = discord.File(midi_output_path, filename=out_name)

        result = base_embed("Chuyển đổi hoàn tất", color=COLOR_OK)
        result.add_field(name="Nguồn", value=f"`{source_name}`", inline=True)
        result.add_field(name="Model", value="Transkun V2", inline=True)
        result.add_field(name="Thời gian xử lý", value=f"{elapsed:.1f} giây", inline=True)

        if stats:
            result.add_field(name="Thời lượng", value=fmt_duration(stats["duration"]), inline=True)
            result.add_field(name="Số nốt nhạc", value=f"{stats['note_count']:,}".replace(",", "."), inline=True)
            result.add_field(name="Mật độ nốt", value=f"{stats['density']} nốt/giây", inline=True)
            result.add_field(name="Tầm âm", value=f"{stats['lowest']} → {stats['highest']}", inline=True)
            result.add_field(name="Tempo ước tính", value=f"{stats['tempo']} BPM", inline=True)
            result.add_field(name="Vận tốc TB", value=f"{stats['avg_velocity']} / 127", inline=True)
        else:
            result.add_field(name="Thống kê MIDI", value=f"Không đọc được chi tiết ({analyze_error})", inline=False)

        await interaction.edit_original_response(embed=result, attachments=[midi_file])


@client.event
async def on_ready():
    activity = discord.Activity(type=discord.ActivityType.watching, name="/transcribe | File/Link → MIDI")
    await client.change_presence(status=discord.Status.idle, activity=activity)

    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        tree.copy_global_to(guild=guild)
        synced = await tree.sync(guild=guild)
        print(f"Đã sync thành công {len(synced)} lệnh cho Guild ID: {GUILD_ID}")
    else:
        synced = await tree.sync()
        print(f"Đã sync thành công {len(synced)} lệnh Global.")

    print(f"Đã đăng nhập thành công: {client.user} (ID: {client.user.id})")


def main():
    if not TOKEN:
        raise SystemExit("Thiếu biến môi trường DISCORD_TOKEN.")
    client.run(TOKEN)


if __name__ == "__main__":
    main()
