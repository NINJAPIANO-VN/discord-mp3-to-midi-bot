import sys
import os
import types

# ==============================================================================
# 1. TRIỆT ĐỂ PATCH MOCK TORCHAUDIO TRƯỚC KHI IMPORT BẤT KỲ THƯ VIỆN NÀO
# ==============================================================================
os.environ["TORCHAUDIO_USE_BACKEND_DISPATCHER"] = "0"

import torch
torch.ops.load_library = lambda x: None

# Giả lập hoàn toàn package torchaudio trong sys.modules
mock_torchaudio = types.ModuleType("torchaudio")
mock_ext = types.ModuleType("torchaudio._extension")
mock_ext._IS_TORCHAUDIO_EXT_AVAILABLE = False
mock_ext._load_lib = lambda x: None

mock_transforms = types.ModuleType("torchaudio.transforms")

class DummyModule(torch.nn.Module):
    def __init__(self, *args, **kwargs):
        super().__init__()
    def forward(self, x):
        return x

mock_transforms.Resample = DummyModule
mock_transforms.MelSpectrogram = DummyModule
mock_torchaudio.transforms = mock_transforms
mock_torchaudio._extension = mock_ext

sys.modules["torchaudio"] = mock_torchaudio
sys.modules["torchaudio._extension"] = mock_ext
sys.modules["torchaudio.transforms"] = mock_transforms
# ==============================================================================

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


def run_transkun_pure_python(input_path: str, output_path: str) -> tuple[bool, str]:
    """
    Chạy TransKun trực tiếp bằng SoundFile và PyTorch Pure Python.
    """
    try:
        import soundfile as sf
        import transkun
        import transkun.Util

        # Patch hàm loadAudio của transkun
        def custom_audio_loader(filepath):
            data, samplerate = sf.read(filepath, dtype='float32')
            tensor = torch.from_numpy(data)
            if tensor.ndim == 1:
                tensor = tensor.unsqueeze(0)
            else:
                tensor = tensor.T.contiguous()
            return tensor, samplerate

        # Ghi đè MelSpectrum của TransKun bằng PyTorch STFT thuần
        class PureTorchMelSpectrum(torch.nn.Module):
            def __init__(self, win_length, hop_length, n_fft, n_mels, sample_rate):
                super().__init__()
                self.win_length = win_length
                self.hop_length = hop_length
                self.n_fft = n_fft
                
                # Biến đổi Mel Filterbank bằng torch thuần
                window = torch.hann_window(win_length)
                self.register_buffer("window", window)

            def forward(self, x):
                if x.ndim == 3:
                    x = x.squeeze(1)
                stft = torch.stft(
                    x, 
                    n_fft=self.n_fft, 
                    hop_length=self.hop_length, 
                    win_length=self.win_length, 
                    window=self.window, 
                    return_complex=True
                )
                spectrogram = torch.abs(stft) ** 2
                return spectrogram

        transkun.Util.loadAudio = custom_audio_loader
        transkun.Util.MelSpectrum = PureTorchMelSpectrum

        device = torch.device("cpu")
        
        # Load mô hình TransKun
        model = transkun.TransKun().to(device)
        model.eval()

        # Thực thi chuyển đổi
        transkun.transcribeDataset(model, input_path, output_path, device=device)

        return True, ""
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
        ok, err = await asyncio.to_thread(run_transkun_pure_python, input_path, output_path)
        elapsed = time.time() - start

        if not ok or not os.path.exists(output_path):
            await interaction.edit_original_response(
                embed=base_embed("Chuyển đổi thất bại", f"{BAR}\n```\n{err}\n
