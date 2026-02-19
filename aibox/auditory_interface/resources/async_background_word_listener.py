import asyncio
import uuid
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

import sounddevice as sd
import soundfile as sf
import whisper


# ---------- Configuration ----------

WAKE_WORD = "hey"
SAMPLE_RATE = 16000
CHANNELS = 1

SNIPPET_DURATION = 1.0   # seconds per wake-listening chunk
COMMAND_DURATION = 5.0   # seconds for full command recording

# Load Whisper models once (you can use same model for both to simplify)
wake_model = whisper.load_model("base")    # for wake word (fast)
command_model = whisper.load_model("base") # for command (more accurate)


# ---------- Blocking helpers (used via asyncio.to_thread) ----------

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
        fp16=False,  # set True if you have a GPU with FP16
    )
    return (result.get("text") or "").strip()


# ---------- Async wrappers ----------

async def record_snippet_async(duration: float = SNIPPET_DURATION) -> str:
    return await asyncio.to_thread(_record_blocking, duration, "WAKE_SNIPPET")


async def record_command_async(duration: float = COMMAND_DURATION) -> str:
    return await asyncio.to_thread(_record_blocking, duration, "COMMAND")


async def transcribe_with_model_async(path: str, model: whisper.Whisper) -> str:
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
    print(f"[WAKE] Listening for wake word '{wake_word.upper()}' (local Whisper)...")

    while True:
        snippet_path = await record_snippet_async(duration=snippet_duration)

        try:
            text = (await transcribe_with_model_async(snippet_path, wake_model)).lower()
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

async def capture_and_transcribe_command_async() -> str:
    """
    After wake word is detected, record a longer command and transcribe it.
    Returns the recognized text.
    """
    print("\n[COMMAND] Please speak your command now...")
    cmd_path = await record_command_async()

    print("[COMMAND] Transcribing command with local Whisper...")
    try:
        cmd_text = await transcribe_with_model_async(cmd_path, command_model)
    except Exception as e:
        print(f"[COMMAND][ERROR] STT failed: {e}")
        return ""

    if cmd_text:
        print(f"[COMMAND TEXT] {cmd_text}\n")
    else:
        print("[COMMAND TEXT] (no speech recognized)\n")

    return cmd_text


# ---------- Background listener loop ----------

async def wake_word_listener_loop(
    handle_command: Callable[[str], Awaitable[None]],
) -> None:
    """
    Runs forever:
      - waits for wake word
      - captures + transcribes a command
      - passes the text to `handle_command`
      - then goes back to listening

    Designed to be run as a background task with asyncio.create_task().
    """
    while True:
        await listen_for_wake_word_whisper_async()
        cmd_text = await capture_and_transcribe_command_async()
        if cmd_text:
            await handle_command(cmd_text)
        # Then it automatically loops back to listening for HANS again


# ---------- Example command handler (plug in LLM + MCP here) ----------

async def handle_command_text(command_text: str) -> None:
    """
    This is where you integrate your behavior:
      - call LLM with tools
      - trigger MCP tools
      - log, update UI, etc.
    """
    print(f"[HANDLER] Received command: {command_text}")
    # Example placeholder:
    # await my_llm_mcp_orchestrator.handle_text_command_async(command_text)


# ---------- App entrypoint ----------

async def main():
    print("=== Async Background Wake Word Listener Demo ===")
    print(f"Say '{WAKE_WORD.upper()} ...' to issue a command.\n")

    # Start the wake-word listener as a background task
    listener_task = asyncio.create_task(
        wake_word_listener_loop(handle_command_text),
        name="wake_word_listener",
    )

    try:
        # Here, your app can do other async work in parallel.
        # For demo, we just keep the event loop alive.
        while True:
            await asyncio.sleep(3600)

    except KeyboardInterrupt:
        print("\n[MAIN] KeyboardInterrupt, shutting down...")
        listener_task.cancel()
        try:
            await listener_task
        except asyncio.CancelledError:
            print("[MAIN] Listener task cancelled.")


if __name__ == "__main__":
    asyncio.run(main())