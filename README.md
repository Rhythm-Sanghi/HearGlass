# HearGlass

A Windows desktop overlay that transcribes system audio into subtitles. It
captures speaker/headphone output through WASAPI loopback and runs
faster-whisper locally, with Silero VAD for speech onset and an energy-based
silence detector. faster-whisper uses OpenAI's Whisper models through CTranslate2.

## Install

Download `HearGlass.exe` from the [v1.0.5 release](https://github.com/Rhythm-Sanghi/HearGlass/releases/tag/v1.0.5).
On first use, the app downloads the selected Whisper and Silero models into a
local cache. Later use requires those models to remain available.

To run from source on Windows with Python 3.10–3.13:

```powershell
git clone https://github.com/Rhythm-Sanghi/HearGlass.git
cd HearGlass
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python subtitle_overlay.py
```

For a command-line capture/transcription check, run
`python transcription_engine.py --model base.en` and play some speech.

## Use

The subtitle region lets clicks pass through. Use the handle to move the window
and right-click it for settings, transcript copying or exit. The system tray
also provides access to the application.

- `Ctrl + Alt + C`: copy the accumulated session transcript.
- `Ctrl + Alt + Q`: exit.
- Settings: choose the audio device, model, language, compute device and width.
  Values are saved in local `config.json`.

`tiny.en` and `base.en` are English-only model choices. For another language,
choose a multilingual Whisper model and the appropriate language setting.
`auto` enables language detection for transcription; the current pipeline does
not request translation into another language.

## How transcription works

Captured audio is gain-adjusted, segmented by speech/silence detectors and queued
for faster-whisper. The CPU setup uses quantization and greedy decoding. A text
filter removes some common repeated model outputs before rendering subtitles.

Gain control can amplify quiet speech and noise. VAD can miss speech or trigger
on non-speech, and Whisper can produce incorrect words. Latency and accuracy
depend on the audio, model and hardware; no zero-error or device-independent
performance guarantee is made. This transcript should be checked against the
audio where accuracy matters.

## Troubleshooting

| Issue | Try |
|---|---|
| Delayed subtitles | Use a smaller model; compatible CUDA hardware is optional. |
| No transcription | Check the selected loopback device and engine error output. |
| Muted audio is not captured | The silent-capture workaround may not work for every device; try unmuting slightly. |
| Repeated or incorrect text | Check audio quality and VAD thresholds; the text filter cannot catch every error. |

## Build an executable

```powershell
python -m pip install pyinstaller
pyinstaller HearGlass.spec --noconfirm
```

The output is `dist\HearGlass.exe`. Keep generated executables and local model
caches out of source control. See [LICENSE](LICENSE) for the application license.
