import sys
import os

# Tắt warning dispatcher của torchaudio trên môi trường CPU
os.environ["TORCHAUDIO_USE_BACKEND_DISPATCHER"] = "0"

import asyncio
import tempfile
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
            text="Transkun V2 · MP3 → MIDI",
            icon_url=client.user.display_avatar.url,
        )
    embed.timestamp = discord.utils.utcnow()
    return embed


def fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def run_transkun_official(input_path: str, output_path: str) -> tuple[bool, str]:
    """
    Gọi trực tiếp entrypoint main() của TransKun qua sys.argv.
    """
    try:
        from transkun.transcribe import main as transkun_main

        # Lưu lại sys.argv gốc
        old_argv = sys.argv
        sys.argv = ["transkun", input_path, output_path, "--device", "cpu"]

        try:
            transkun_main()
        finally:
            sys.argv = old_argv

        if os.path.exists(output_path):
            return True, ""
        return False, "File MIDI không được khởi tạo sau khi transcribe."
    except Exception as e:
        import traceback
        return False, f"{str(e)}\n{traceback.format_exc()[-1000:]}"


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


@tree.command(name="transcribe", description="Chuyển file MP3/WAV piano thành MIDI bằng Transkun V2")
@app_commands.describe(file="File âm thanh piano cần chuyển (mp3, wav, m4a, ogg, flac)")
async def transcribe(interaction: discord.Interaction, file: discord.Attachment):
    ext = Path(file.filename).suffix.lower()

    if ext not in ALLOWED_EXT:
        await interaction.response.send_message(
            embed=base_embed("Định dạng không hỗ trợ", f"Chỉ chấp nhận: `{', '.join(sorted(ALLOWED_EXT))}`", color=COLOR_ERR),
            ephemeral=True,
        )
        return

    if file.size > MAX_FILE_MB * 1024 * 1024:
        await interaction.response.send_message(
            embed=base_embed("File quá lớn", f"Giới hạn hiện tại: **{MAX_FILE_MB} MB**.", color=COLOR_ERR),
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    working = base_embed(
        "Đang xử lý",
        f"{BAR}\nĐang chuyển **{file.filename}** sang MIDI với Transkun V2...\nQuá trình này có thể mất vài phút tuỳ độ dài bản nhạc.",
        color=COLOR_WORKING,
    )
    await interaction.edit_original_response(embed=working)

    with tempfile.TemporaryDirectory() as tmp:
        input_path = os.path.join(tmp, f"input{ext}")
        output_path = os.path.join(tmp, "output.mid")

        audio_bytes = await file.read()
        with open(input_path, "wb") as f:
            f.write(audio_bytes)

        start = time.time()
        ok, err = await asyncio.to_thread(run_transkun_official, input_path, output_path)
        elapsed = time.time() - start

        if not ok or not os.path.exists(output_path):
            await interaction.edit_original_response(
                embed=base_embed("Chuyển đổi thất bại", f"{BAR}\n```\n{err}\n```", color=COLOR_ERR)
            )
            return

        try:
            stats = await asyncio.to_thread(analyze_midi, output_path)
        except Exception as e:
            stats = None
            analyze_error = str(e)

        out_name = Path(file.filename).stem + ".mid"
        midi_file = discord.File(output_path, filename=out_name)

        result = base_embed("Chuyển đổi hoàn tất", color=COLOR_OK)
        result.add_field(name="File gốc", value=f"`{file.filename}`", inline=True)
        result.add_field(name="Dung lượng", value=f"{file.size / 1024:.1f} KB", inline=True)
        result.add_field(name="Model", value="Transkun V2", inline=True)

        if stats:
            result.add_field(name="Thời lượng", value=fmt_duration(stats["duration"]), inline=True)
            result.add_field(name="Số nốt nhạc", value=f"{stats['note_count']:,}".replace(",", "."), inline=True)
            result.add_field(name="Mật độ nốt", value=f"{stats['density']} nốt/giây", inline=True)
            result.add_field(name="Tầm âm", value=f"{stats['lowest']} → {stats['highest']}", inline=True)
            result.add_field(name="Tempo ước tính", value=f"{stats['tempo']} BPM", inline=True)
            result.add_field(name="Vận tốc TB", value=f"{stats['avg_velocity']} / 127", inline=True)
        else:
            result.add_field(name="Thống kê MIDI", value=f"Không đọc được chi tiết ({analyze_error})", inline=False)

        result.add_field(name="Thời gian xử lý", value=f"{elapsed:.1f} giây", inline=True)

        await interaction.edit_original_response(embed=result, attachments=[midi_file])


@client.event
async def on_ready():
    activity = discord.Activity(type=discord.ActivityType.watching, name="/transcribe | MP3 → MIDI")
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
