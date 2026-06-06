"""
transcription_engine.py — CLI Real-Time Transcription Engine
=============================================================
Tests the full audio capture → VAD → Whisper pipeline before the GUI.

Usage:
    python transcription_engine.py [--device INDEX] [--model tiny.en|base.en]
                                   [--language en|auto|fr|...] [--compute-device auto|cpu|cuda]

If --device is omitted, the script auto-detects the first WASAPI loopback
device. Run list_devices.py first to find the correct index.

Example:
    python transcription_engine.py --device 4 --model base.en --language en

Press Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import queue
import re
import sys
import threading
import time
import warnings
from collections import deque
from typing import Callable, Generator

import numpy as np

warnings.filterwarnings("ignore", category=UserWarning)

# ── Logging setup ─────────────────────────────────────────────────────────────
# All modules share the same "subtitle" logger hierarchy.
# File output goes to app.log; console output goes to stderr.

def _setup_logging() -> logging.Logger:
    logger = logging.getLogger("subtitle.engine")
    if logger.handlers:
        return logger  # Already configured (e.g. imported by overlay)
    root = logging.getLogger("subtitle")
    if root.handlers:
        return logger

    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"
    )
    # Rotating file handler — keeps at most 3 × 5 MB = 15 MB of history.
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.log")
    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    return logger

log = _setup_logging()


# Silent-capture helper — keeps the loopback stream alive even when
# the user has their speakers muted.
try:
    from silent_capture import SilentCaptureContext
except ImportError:
    class SilentCaptureContext:  # type: ignore[no-redef]
        def enable(self): pass
        def disable(self): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass

# ── Optional imports with graceful errors ─────────────────────────────────────
try:
    import soundcard as sc
except ImportError:
    log.critical("soundcard not installed. Run: pip install soundcard")
    sys.exit(1)

try:
    from faster_whisper import WhisperModel
except ImportError:
    log.critical("faster-whisper not installed. Run: pip install faster-whisper")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Constants & Configuration
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE_RATE: int    = 16_000        # Whisper expects 16 kHz
CHANNELS: int       = 1             # Mono

# 64 ms gives soundcard's MediaFoundation backend enough headroom to avoid
# the "data discontinuity" warnings that occur with a 32 ms chunk size.
# Silero VAD officially supports ≥ 30 ms chunks at 16 kHz, so 64 ms is fine.
CHUNK_MS: int       = 64
CHUNK_SAMPLES: int  = int(SAMPLE_RATE * CHUNK_MS / 1000)  # 1 024 samples

BUFFER_SECONDS: float  = 3.0        # max speech segment fed to Whisper
SILENCE_SECONDS: float = 0.4        # trailing silence before flushing
SILENCE_CHUNKS: int    = int(SILENCE_SECONDS * 1000 / CHUNK_MS)

VAD_DB_THRESHOLD: float = -35.0     # EnergyVAD dB threshold
SILERO_THRESHOLD: float  = 0.45     # Silero confidence threshold (0–1)

WHISPER_MODEL: str   = "base.en"
LANGUAGE: str | None = "en"         # set None for auto-detect (adds ~0.5 s/segment)
COMPUTE_DEVICE: str  = "auto"       # "auto" | "cpu" | "cuda"


# ─────────────────────────────────────────────────────────────────────────────
# Whisper hallucination filter
# ─────────────────────────────────────────────────────────────────────────────

# Phrases Whisper commonly hallucinates on silence / background music.
_HALLUCINATIONS: frozenset[str] = frozenset({
    "thank you for watching",
    "thanks for watching",
    "please subscribe",
    "like and subscribe",
    "subscribe to the channel",
    "thanks for listening",
    "subtitles by",
    "captions by",
    "transcribed by",
    "www.",
    ".",
    "...",
    # Common filler / punctuation-only outputs
    "you",
    "uh",
    "um",
})

# Bracket/paren tokens: [Music], [Applause], (crowd cheers), etc.
# Whisper emits these on music, ambient sound, and silence.
_BRACKET_PATTERN: re.Pattern = re.compile(r"^\s*[\(\[].{0,40}[\)\]]\s*$")


def _is_hallucination(text: str) -> bool:
    """Return True if *text* looks like a Whisper hallucination."""
    stripped = text.strip()
    if len(stripped) < 2:
        return True
    if _BRACKET_PATTERN.match(stripped):          # [Music], (Applause), etc.
        return True
    normalized = stripped.lower().rstrip(".!?,;")
    return normalized in _HALLUCINATIONS or normalized.startswith("www.")


# ─────────────────────────────────────────────────────────────────────────────
# VAD helpers
# ─────────────────────────────────────────────────────────────────────────────

class EnergyVAD:
    """
    Simple RMS energy voice-activity detector — zero-dependency fallback.
    Also used as the *silence* detector during confirmed speech segments
    because it is much cheaper than running Silero on every chunk.
    """

    def __init__(self, threshold_db: float = VAD_DB_THRESHOLD) -> None:
        self.threshold_db = threshold_db

    def is_speech(self, pcm_int16: np.ndarray) -> bool:
        if len(pcm_int16) == 0:
            return False
        rms = np.sqrt(np.mean(pcm_int16.astype(np.float32) ** 2))
        if rms < 1e-9:
            return False
        db = 20 * np.log10(rms / 32768.0)
        return db > self.threshold_db


class SileroVAD:
    """
    Wrapper around the Silero VAD model via torch.hub.
    Used exclusively for *speech onset* detection (not silence detection)
    to get the best sensitivity while limiting inference cost.
    """

    def __init__(self, threshold: float = SILERO_THRESHOLD) -> None:
        self.threshold = threshold
        self._model = None
        self._torch = None
        self._load()

    def _load(self) -> None:
        try:
            import torch
            log.info("Loading Silero VAD model (first run downloads ~1 MB)...")
            model, utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                trust_repo=True,
                verbose=False,
            )
            self._model = model
            self._torch = torch
            log.info("Silero VAD loaded successfully.")
        except Exception as exc:
            log.warning("Silero VAD load failed (%s). Falling back to EnergyVAD.", exc)
            self._model = None

    def is_speech(self, pcm_int16: np.ndarray) -> bool:
        if self._model is None:
            return False
        # Silero VAD v4+ requires exactly 512 samples at 16 kHz.
        # We split the input array into chunks of 512 samples.
        chunk_size = 512
        for i in range(0, len(pcm_int16), chunk_size):
            chunk = pcm_int16[i:i+chunk_size]
            if len(chunk) < chunk_size:
                # Pad with zeros if it's too short
                pad = np.zeros(chunk_size - len(chunk), dtype=pcm_int16.dtype)
                chunk = np.concatenate([chunk, pad])
            
            audio_float = chunk.astype(np.float32) / 32768.0
            tensor = self._torch.from_numpy(audio_float)
            with self._torch.no_grad():
                confidence = self._model(tensor, SAMPLE_RATE).item()
            if confidence >= self.threshold:
                return True
        return False

    @property
    def available(self) -> bool:
        return self._model is not None


# ─────────────────────────────────────────────────────────────────────────────
# Audio chunk generator
# ─────────────────────────────────────────────────────────────────────────────

def audio_chunk_generator(
    mic,
    onset_vad:   SileroVAD | EnergyVAD,
    silence_vad: EnergyVAD,
    stop_event:  threading.Event,
) -> Generator[np.ndarray, None, None]:
    """
    Continuously reads from *mic*, applies AGC + dual-VAD, and yields
    contiguous speech segments as int16 numpy arrays.

    Dual-VAD strategy:
      • *onset_vad*   (Silero when available) — detects the *start* of speech.
        Best sensitivity; run only when NOT already in a speech segment.
      • *silence_vad* (EnergyVAD) — detects *silence within/after* speech.
        Cheaper; adequate once we know we are inside a segment.

    AGC note:
      VAD receives *raw* (un-boosted) PCM so that gain does not artificially
      trip the onset detector or distort Silero confidence scores.
      The Whisper buffer stores *boosted* PCM for best transcription quality.
    """
    speech_buffer: list[np.ndarray] = []
    silence_count: int = 0
    in_speech:     bool = False
    max_chunks = int(BUFFER_SECONDS * 1000 / CHUNK_MS)
    pre_roll:  deque[np.ndarray] = deque(maxlen=5)

    # AGC state — maintained on raw float signal (before int16 conversion).
    # running_max tracks the peak amplitude seen recently.
    running_max: float = 1000.0                     # start low → fast initial gain
    AGC_TARGET:  float = 32768.0 * 0.7             # target 70 % of full scale

    with mic.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS) as recorder:
        while not stop_event.is_set():
            data = recorder.record(numframes=CHUNK_SAMPLES)  # float32 in [-1, 1]
            if data.ndim == 2:
                data = data.mean(axis=1)

            # ── Raw PCM (for VAD) ─────────────────────────────────────────────
            # Keep gain out of the VAD path so Silero confidence scores and the
            # energy threshold are not skewed by the gain stage.
            pcm_raw = np.clip(data * 32767, -32768, 32767).astype(np.int16)

            # ── AGC ───────────────────────────────────────────────────────────
            chunk_peak = float(np.max(np.abs(data))) * 32768.0
            if chunk_peak > 50.0:                   # skip digital-silence frames
                if chunk_peak > running_max:
                    running_max = chunk_peak         # instant attack
                else:
                    running_max = running_max * 0.98 + chunk_peak * 0.02  # slow decay
            else:
                running_max = running_max * 0.99    # very slow decay during silence

            gain = min(AGC_TARGET / max(running_max, 100.0), 50.0)  # cap at 50×
            pcm_boosted = np.clip(data * gain * 32767, -32768, 32767).astype(np.int16)

            # ── Dual-VAD ─────────────────────────────────────────────────────
            # Outside speech: use onset_vad (Silero) for best sensitivity.
            # Inside speech:  use silence_vad (EnergyVAD) — cheaper, sufficient
            #                 for detecting the end of a segment.
            if in_speech:
                is_speech = silence_vad.is_speech(pcm_raw)
            else:
                is_speech = onset_vad.is_speech(pcm_raw)

            # ── Buffering ────────────────────────────────────────────────────
            if is_speech:
                if not in_speech:
                    speech_buffer.extend(pre_roll)  # prepend pre-roll context
                    in_speech     = True
                    silence_count = 0
                speech_buffer.append(pcm_boosted)   # store AGC-boosted audio

                if len(speech_buffer) >= max_chunks:   # prevent unbounded growth
                    yield np.concatenate(speech_buffer)
                    speech_buffer = []

            else:
                pre_roll.append(pcm_boosted)
                if in_speech:
                    silence_count += 1
                    speech_buffer.append(pcm_boosted)  # trailing silence for context
                    if silence_count >= SILENCE_CHUNKS:
                        if speech_buffer:
                            yield np.concatenate(speech_buffer)
                        speech_buffer = []
                        silence_count = 0
                        in_speech     = False


# ─────────────────────────────────────────────────────────────────────────────
# Compute device resolution
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_compute(compute_device: str) -> tuple[str, str]:
    """
    Return *(device, compute_type)* for WhisperModel based on *compute_device*.

    ============  ===========  ================
    compute_device  device       compute_type
    ============  ===========  ================
    "auto"         cuda/cpu      float16/int8
    "cuda"         cuda          float16
    "cpu"          cpu           int8
    ============  ===========  ================
    """
    if compute_device == "cpu":
        return "cpu", "int8"
    try:
        import torch
        if torch.cuda.is_available():
            log.info("CUDA detected — using GPU inference (float16).")
            return "cuda", "float16"
        elif compute_device == "cuda":
            log.warning("CUDA requested but not available. Falling back to CPU.")
    except ImportError:
        if compute_device == "cuda":
            log.warning("torch not importable — cannot use CUDA. Falling back to CPU.")
    log.info("Using CPU inference (int8).")
    return "cpu", "int8"


# ─────────────────────────────────────────────────────────────────────────────
# Transcription Engine
# ─────────────────────────────────────────────────────────────────────────────

class TranscriptionEngine:
    """
    Producer-Consumer transcription engine.

    Producer thread  → captures audio, runs dual-VAD + AGC, enqueues segments
    Consumer thread  → dequeues segments, runs Whisper, calls on_transcript

    Callbacks (all thread-safe; schedule UI updates via widget.after()):
      on_transcript(text: str)         — new transcription result
      on_error(message: str)           — producer thread died mid-session
    """

    def __init__(
        self,
        device_index:   int | None = None,
        model_name:     str        = WHISPER_MODEL,
        language:       str | None = LANGUAGE,
        compute_device: str        = COMPUTE_DEVICE,
        on_transcript:  Callable[[str], None] | None = None,
        on_error:       Callable[[str], None] | None = None,
    ) -> None:
        self.device_index   = device_index
        self.model_name     = model_name
        self.language       = language          # None → auto-detect
        self.compute_device = compute_device

        self.on_transcript = on_transcript or (lambda t: log.info("TRANSCRIPT: %s", t))
        self.on_error      = on_error

        self._audio_queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=10)
        self._stop_event = threading.Event()
        self._producer: threading.Thread | None = None
        self._consumer: threading.Thread | None = None

        self._mic    = None
        self._model: WhisperModel | None = None
        self._onset_vad:   SileroVAD | EnergyVAD | None = None
        self._silence_vad: EnergyVAD | None              = None

        self._silent_ctx: SilentCaptureContext = SilentCaptureContext()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._initialize()
        self._stop_event.clear()

        self._producer = threading.Thread(
            target=self._produce, daemon=True, name="AudioProducer"
        )
        self._consumer = threading.Thread(
            target=self._consume, daemon=True, name="WhisperConsumer"
        )
        self._producer.start()
        self._consumer.start()
        log.info("Transcription engine started. Listening...")

    def stop(self) -> None:
        """Gracefully stop both threads. Blocks until they exit."""
        self._stop_event.set()
        if self._producer:
            self._producer.join(timeout=3)
        if self._consumer:
            self._audio_queue.put(None)    # wake the consumer with sentinel
            self._consumer.join(timeout=10)
        self._silent_ctx.disable()
        log.info("Engine stopped.")

    # ── Initialization ────────────────────────────────────────────────────────

    def _initialize(self) -> None:
        # 1. Load VADs
        silero = SileroVAD()
        self._onset_vad   = silero if silero.available else EnergyVAD()
        self._silence_vad = EnergyVAD()   # always energy for in-speech silence
        vad_name = "Silero" if silero.available else "Energy (dB threshold)"
        log.info("Onset VAD: %s  |  Silence VAD: EnergyVAD", vad_name)

        # 2. Enable silent-capture BEFORE opening the recorder so the WASAPI
        #    loopback tap sees real audio even when speakers are muted.
        self._silent_ctx.enable()

        # 3. Select loopback mic
        if self.device_index is not None:
            all_mics = sc.all_microphones(include_loopback=True)
            if 0 <= self.device_index < len(all_mics):
                self._mic = all_mics[self.device_index]
                log.info("Audio: manually selected device [%d] %s",
                         self.device_index, self._mic.name)
            else:
                log.warning(
                    "Device index %d out of range (max %d). Auto-detecting.",
                    self.device_index, len(all_mics) - 1,
                )
                self._mic = self._get_default_loopback()
        else:
            self._mic = self._get_default_loopback()

        # 4. Load Whisper model
        device, compute_type = _resolve_compute(self.compute_device)
        lang_display = self.language or "auto-detect"
        log.info(
            "Loading Whisper '%s' | device=%s | compute=%s | language=%s",
            self.model_name, device, compute_type, lang_display,
        )
        log.info("(First run downloads from HuggingFace — may take a moment)")

        # Auto-select cpu_threads: half of logical cores, minimum 1
        cpu_threads = max(1, (os.cpu_count() or 2) // 2)

        self._model = WhisperModel(
            self.model_name,
            device=device,
            compute_type=compute_type,
            num_workers=1,
            cpu_threads=cpu_threads,
        )
        log.info("Whisper model ready. cpu_threads=%d", cpu_threads)

    def _get_default_loopback(self):
        """Auto-detect the default speaker's WASAPI loopback device."""
        speaker = sc.default_speaker()
        log.info("Default speaker: %s", speaker.name)
        all_mics = sc.all_microphones(include_loopback=True)

        for mic in all_mics:
            if speaker.name in mic.name:
                log.info("Matched loopback: %s", mic.name)
                return mic
        for mic in all_mics:
            if "Microphone" not in mic.name:
                log.info("Fallback loopback: %s", mic.name)
                return mic

        mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        log.info("Last-resort loopback: %s", mic.name)
        return mic

    # ── Producer thread ───────────────────────────────────────────────────────

    def _produce(self) -> None:
        com_initialized = False
        if sys.platform == "win32":
            try:
                import pythoncom
                pythoncom.CoInitialize()
                com_initialized = True
            except Exception:
                try:
                    import ctypes
                    ctypes.windll.ole32.CoInitialize(None)
                    com_initialized = True
                except Exception:
                    pass
        consecutive_drops = 0
        _DROP_WARN_THRESHOLD = 5   # escalate to WARNING after this many in a row

        try:
            for audio_segment in audio_chunk_generator(
                self._mic,
                self._onset_vad,
                self._silence_vad,
                self._stop_event,
            ):
                if self._stop_event.is_set():
                    break
                try:
                    self._audio_queue.put_nowait(audio_segment)
                    consecutive_drops = 0   # reset on successful enqueue
                except queue.Full:
                    consecutive_drops += 1
                    if consecutive_drops >= _DROP_WARN_THRESHOLD:
                        log.warning(
                            "Audio queue full — %d consecutive segments dropped. "
                            "Consider switching to a faster model (e.g. tiny.en).",
                            consecutive_drops,
                        )
                    else:
                        log.debug("Audio queue full — dropping segment (%d).",
                                  consecutive_drops)
        except Exception as exc:
            log.error("AudioProducer fatal error: %s", exc, exc_info=True)
            # Surface the error to the UI (e.g. device disconnected mid-session)
            if self.on_error and not self._stop_event.is_set():
                self.on_error(str(exc))
        finally:
            if com_initialized:
                if sys.platform == "win32":
                    try:
                        import pythoncom
                        pythoncom.CoUninitialize()
                    except Exception:
                        try:
                            import ctypes
                            ctypes.windll.ole32.CoUninitialize()
                        except Exception:
                            pass

    # ── Consumer thread ───────────────────────────────────────────────────────

    def _consume(self) -> None:
        last_text: str = ""
        while True:
            segment = self._audio_queue.get()
            if segment is None:
                break  # sentinel — stop requested
            try:
                text = self._transcribe(segment)
                if text and text != last_text:
                    last_text = text
                    self.on_transcript(text)
            except Exception as exc:
                log.error("WhisperConsumer error: %s", exc, exc_info=True)

    # ── Whisper inference ─────────────────────────────────────────────────────

    def _transcribe(self, audio: np.ndarray) -> str:
        """Run faster-whisper on *audio* (int16) and return cleaned text."""
        audio_f32 = audio.astype(np.float32) / 32768.0

        segments, _info = self._model.transcribe(
            audio_f32,
            language=self.language,             # None → auto-detect
            beam_size=1,                        # 1 is greedy, much faster on CPU
            temperature=0.0,
            vad_filter=False,                   # Disabled (our custom Silero VAD already filtered it)
            condition_on_previous_text=False,   # prevents loops
        )

        parts: list[str] = []
        for seg in segments:
            t = seg.text.strip()
            if t and not _is_hallucination(t):
                parts.append(t)

        return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CLI real-time transcription engine — verify audio capture before using the GUI."
    )
    parser.add_argument(
        "--device", type=int, default=None, metavar="INDEX",
        help="soundcard device index (see list_devices.py). Omit to auto-detect.",
    )
    parser.add_argument(
        "--model", type=str, default=WHISPER_MODEL,
        choices=["tiny.en", "base.en", "small.en", "medium.en"],
        help=f"Whisper model size (default: {WHISPER_MODEL}).",
    )
    parser.add_argument(
        "--language", type=str, default="en", metavar="CODE",
        help="BCP-47 language code (e.g. 'en', 'fr', 'de') or 'auto' for detection. Default: en",
    )
    parser.add_argument(
        "--compute-device", type=str, default="auto",
        choices=["auto", "cpu", "cuda"],
        help="Inference device: auto (prefer CUDA), cpu, or cuda. Default: auto",
    )
    args = parser.parse_args()

    lang = None if args.language.lower() == "auto" else args.language

    print("\n" + "=" * 60)
    print("  REAL-TIME AUDIO TRANSCRIPTION (CLI)")
    print("=" * 60)
    print(f"  Model   : {args.model}")
    print(f"  Device  : {args.device if args.device is not None else 'auto-detect'}")
    print(f"  Language: {lang or 'auto-detect'}")
    print(f"  Compute : {args.compute_device}")
    print("  Press Ctrl+C to stop.\n")

    transcripts: list[str] = []

    def on_transcript(text: str) -> None:
        ts   = time.strftime("%H:%M:%S")
        line = f"[{ts}] {text}"
        transcripts.append(line)
        print(line, flush=True)

    def on_error(msg: str) -> None:
        print(f"\n[ERROR] Engine error: {msg}", flush=True)

    engine = TranscriptionEngine(
        device_index=args.device,
        model_name=args.model,
        language=lang,
        compute_device=args.compute_device,
        on_transcript=on_transcript,
        on_error=on_error,
    )

    try:
        engine.start()
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[INFO] Stopping...")
    finally:
        engine.stop()
        if transcripts:
            print("\n── Session Transcript ──")
            for line in transcripts:
                print(line)


if __name__ == "__main__":
    main()
