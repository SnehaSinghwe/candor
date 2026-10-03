"""Optional voice front end: microphone (or a WAV file) -> Gemini speech-to-text -> the same agent as text mode.
Recording needs `pip install sounddevice` (PortAudio; on macOS allow the Terminal microphone permission).
`--audio file.wav` needs no extra package. Cannot be tested without a mic and a Gemini key."""
import io, wave
from . import safety


def transcribe_file(path):
    mime = "audio/wav" if str(path).lower().endswith(".wav") else "audio/mpeg" if str(path).lower().endswith(".mp3") else "audio/wav"
    with open(path, "rb") as f:
        return safety.gemini_audio_text(f.read(), mime)


def record(seconds=6, rate=16000):
    try:
        import sounddevice as sd
    except ImportError:
        raise RuntimeError("voice recording needs: pip install sounddevice   (or use --audio file.wav)")
    print(f"  listening for {seconds}s...", flush=True)
    data = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="int16"); sd.wait()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(data.tobytes())
    return safety.gemini_audio_text(buf.getvalue(), "audio/wav")
