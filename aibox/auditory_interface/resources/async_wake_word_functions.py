import asyncio
import time
import uuid
import tempfile
from pathlib import Path

import sounddevice as sd
import soundfile as sf
import whisper


# ---------- Configuration ----------

WAKE_WORD = "hey"
SAMPLE_RATE = 16000
CHANNELS = 1
SNIPPET_DURATION = 1.0   # seconds per wake-listening chunk
COMMAND_DURATION = 5.0   # seconds for full command recording

wake_model = whisper.load_model("tiny")  # or "base"


# ---------- Blocking helpers (wrapped with to_thread) ----------

def _record_blocking(duration: float, prefix: str) -> str:
    """
    Blocking microphone recording. Returns path to WAV file.
    """
    print(f"[{prefix}/MIC] Recording for {duration:.1f}s...")
    audio = sd.rec(
        int(duration * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
    )
    sd.wait()
    print(f"[{prefix}/MIC] Recording finished.")

    tmp_dir = Path(tempfile.gettempdir())
    filename = tmp_dir / f"{prefix}_{uuid.uuid4().hex}.wav"
    sf.write(str(filename), audio, SAMPLE_RATE)
    return str(filename)


def _transcribe_blocking(path: str, model: whisper.Whisper) -> str:
    """
    Blocking local Whisper transcription. Returns text.
    """
    result = model.transcribe(
        path,
        language="en",
        fp16=False,  # set True if you have GPU with FP16
    )
    return (result.get("text") or "").strip()


# ---------- Async wrappers ----------

async def record_snippet_async(duration: float = SNIPPET_DURATION) -> str:
    return await asyncio.to_thread(_record_blocking, duration, "WAKE_SNIPPET")


async def record_command_async(duration: float = COMMAND_DURATION) -> str:
    return await asyncio.to_thread(_record_blocking, duration, "COMMAND")


async def transcribe_snippet_async(path: str) -> str:
    return await asyncio.to_thread(_transcribe_blocking, path, wake_model)


async def transcribe_command_async(path: str, model: whisper.Whisper) -> str:
    return await asyncio.to_thread(_transcribe_blocking, path, model)


# ---------- Async wake-word listener (local Whisper) ----------

async def listen_for_wake_word_whisper_async(
    wake_word: str = WAKE_WORD,
    snippet_duration: float = SNIPPET_DURATION,
    pause_between_snippets: float = 0.1,
) -> None:
    """
    Asynchronously:
      - record short snippets in a worker thread
      - transcribe them with local Whisper in a worker thread
      - check for wake_word

    Returns when the wake word is detected.
    """
    wake_word = wake_word.lower()
    print(f"Listening for wake word '{wake_word.upper()}' (local Whisper, async)...")

    while True:
        snippet_path = await record_snippet_async(duration=snippet_duration)

        try:
            text = (await transcribe_snippet_async(snippet_path)).lower()
        except Exception as e:
            print(f"[WAKE/WHISPER][ERROR] {e}")
            await asyncio.sleep(pause_between_snippets)
            continue

        if not text:
            print("[WAKE/WHISPER] (no speech recognized)")
        else:
            print(f"[WAKE/WHISPER] Heard: '{text}'")
            if wake_word in text:
                print(f"[WAKE/WHISPER] Wake word '{wake_word.upper()}' detected!")
                return

        await asyncio.sleep(pause_between_snippets)


# ---------- Async command capture pipeline ----------

# Use a larger model for the actual command if you like:
command_model = whisper.load_model("base")  # or reuse `wake_model` for simplicity

async def capture_and_transcribe_command_async() -> str:
    """
    After wake word is detected, record a longer command and transcribe it.
    Returns the recognized text.
    """
    print("\n[COMMAND] Please speak your command now...")
    cmd_path = await record_command_async()

    print("[COMMAND] Transcribing command with local Whisper...")
    try:
        cmd_text = await transcribe_command_async(cmd_path, command_model)
    except Exception as e:
        print(f"[COMMAND][ERROR] STT failed: {e}")
        return ""

    if cmd_text:
        print(f"[COMMAND TEXT] {cmd_text}\n")
    else:
        print("[COMMAND TEXT] (no speech recognized)\n")

    return cmd_text


# ---------- Async main loop ----------

async def main():
    print("=== Async Local Whisper Wake Word → Command Demo ===")
    print(f"Say '{WAKE_WORD.upper()}' to start a command.\n")

    try:
        while True:
            # 1) Wait asynchronously for wake word
            await listen_for_wake_word_whisper_async()

            # 2) Capture and transcribe command
            command_text = await capture_and_transcribe_command_async()

            # 3) Here you can plug in your async LLM/MCP pipeline, e.g.:
            #    await handle_text_command_async(command_text)

            user = input("Continue listening for wake word? (Y/n): ").strip().lower()
            if user == "n":
                print("Exiting.")
                break

    except KeyboardInterrupt:
        print("\nInterrupted by user. Exiting.")


if __name__ == "__main__":
    asyncio.run(main())