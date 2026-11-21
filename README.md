# HearGlass

HearGlass puts captions for system audio in a click-through overlay on Windows. It captures your speaker or headphone output through WASAPI loopback and transcribes it locally with faster-whisper.

## Install on Windows

Download `HearGlass.exe` from the [latest release](https://github.com/Rhythm-Sanghi/HearGlass/releases/latest) and launch it. The first run needs an internet connection to download the speech models. Play audio on your default output device to check that captions appear.

Use the top handle to move the overlay. Right-click it to choose an audio device, change the model, or open settings. Press `Ctrl+Alt+C` to copy the session transcript and `Ctrl+Alt+Q` to quit.

## Run from source

Use Python 3.10–3.13. From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python subtitle_overlay.py
```

To isolate capture or transcription problems, run `.\.venv\Scripts\python transcription_engine.py --model base.en` and check the terminal while audio plays. If captions lag on CPU, try the smaller `tiny.en` model. Use a multilingual model for speech outside English.

## How it works

[`silent_capture.py`](silent_capture.py) captures audio, [`transcription_engine.py`](transcription_engine.py) detects speech and runs faster-whisper, and [`subtitle_overlay.py`](subtitle_overlay.py) renders the captions and handles desktop controls.

The executable is built from [`HearGlass.spec`](HearGlass.spec) with PyInstaller. The source is [MIT licensed](LICENSE).
