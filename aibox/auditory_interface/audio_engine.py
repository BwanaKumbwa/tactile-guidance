import asyncio
import os
import uuid
import tempfile
import sounddevice as sd
import soundfile as sf
import whisper
from pathlib import Path

class AudioEngine:
    def __init__(self, wake_word="hey", snippet_duration=2.0, command_duration=5.0):
        self.wake_word = wake_word
        self.snippet_duration = snippet_duration
        self.command_duration = command_duration
        self.sample_rate = 16000
        self.channels = 1
        
        print("--- [AudioEngine] Loading Whisper Models... ---")
        self.wake_model = whisper.load_model("base")
        self.command_model = whisper.load_model("base")
        print("--- [AudioEngine] Models Loaded ---")

    def _record_blocking(self, duration: float, prefix: str) -> str:
        """Blocking microphone recording."""
        if prefix == "COMMAND":
            print(f"   [MIC] Recording {duration}s...")
            
        audio = sd.rec(
            int(duration * self.sample_rate),
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
        )
        sd.wait()
        
        tmp_dir = Path(tempfile.gettempdir())
        filename = tmp_dir / f"{prefix}_{uuid.uuid4().hex}.wav"
        sf.write(str(filename), audio, self.sample_rate)
        return str(filename)

    def _transcribe_blocking(self, path: str, model: whisper.Whisper) -> str:
        """Blocking local Whisper transcription."""
        # fp16=False allows it to run on CPU without warnings
        result = model.transcribe(path, language="en", fp16=False)
        return (result.get("text") or "").strip()

    async def record_audio_async(self, duration: float, prefix: str) -> str:
        return await asyncio.to_thread(self._record_blocking, duration, prefix)

    async def transcribe_async(self, path: str, model: whisper.Whisper) -> str:
        return await asyncio.to_thread(self._transcribe_blocking, path, model)

    async def listen_for_wake_word(self):
        """Loops and prints what it hears until the wake word is detected."""
        print(f"\n[LISTENING] Say '{self.wake_word.upper()}' to activate...")
        
        while True:
            path = await self.record_audio_async(self.snippet_duration, "WAKE")
            
            try:
                text = (await self.transcribe_async(path, self.wake_model)).lower()
                os.remove(path) 
            except Exception:
                continue

            clean_log_text = text.strip()
            if clean_log_text:
                print(f"[WAKE LOOP] Heard: '{clean_log_text}'")

            # Simple check
            clean_text = text.replace(".", "").replace("!", "").replace("?", "").strip()
            
            if self.wake_word in clean_text:
                print(f"[WAKE DETECTED] Triggered by: '{clean_text}'")
                return
            
            await asyncio.sleep(0.1)

    async def capture_command(self) -> str:
        """Records command and returns text."""
        print("   [COMMAND] Speak now...")
        path = await self.record_audio_async(self.command_duration, "COMMAND")
        
        print("   [TRANSCRIBING] Processing...")
        text = await self.transcribe_async(path, self.command_model)
        
        try:
            os.remove(path)
        except:
            pass
            
        return text