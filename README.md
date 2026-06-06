# ▓▓▓ Real-Time Audio Subtitle Overlay ▓▓▓

A transparent, always-on-top Windows desktop overlay that captures system audio (speakers/headphones) in real-time and displays auto-generated subtitles. Powered by **faster-whisper** (OpenAI's Whisper engine optimized via CTranslate2 int8 quantization) and Silero VAD.

---

## 🌟 Key Features

* **Zero Virtual Cables Needed:** Captures raw speaker audio natively via WASAPI Loopback. Works out-of-the-box on Windows 10/11.
* **Fully Click-Through:** Subtitle area is completely click-through, allowing you to use it on top of games, videos, meetings, and browsers without blocking mouse interaction.
* **Intelligent Double-VAD + AGC:**
  * **Silero VAD** detects speech start (high sensitivity, zero false starts).
  * **EnergyVAD** monitors mid-speech silence (lightweight, responsive).
  * **Automatic Gain Control (AGC)** boosts quiet audio (up to 50×) for Whisper so it never misses whisper-quiet speech.
* **Interactive Handle Bar:** A sleek, self-hiding top handle allows you to reposition the overlay. Right-click to access Settings, Copy Transcript, or Quit.
* **Global Hotkeys:** 
  * `Ctrl + Alt + C` copy the session's full accumulated transcript to your clipboard.
  * `Ctrl + Alt + Q` exit the app immediately from anywhere.
* **System Tray Integration:** Minimize the app to the system tray to keep your desktop clean.
* **Optimized for CPU:** Pre-tuned for consumer laptops using greedy decoding, reduced worker contention, and single-pass VAD, making transcription fast and highly accurate even without a dedicated GPU.

---

## 🚀 Installation & Setup

You can either run the app directly using the pre-compiled standalone executable (recommended for laptop users) or run it from source.

### Option A: Download Pre-compiled Binary (No Python Required)
1. Head over to the **[Releases](../../releases)** page.
2. Download the latest `SubtitleOverlay.exe`.
3. Double-click to launch! 
*(Note: On the first run, it will automatically download the Whisper base model ~140 MB and Silero VAD model ~1 MB to your local cache. This may take a moment depending on your internet connection.)*

---

### Option B: Run from Source (Requires Python 3.10 – 3.13)

#### 1. Clone & Set Up Environment
```bash
git clone https://github.com/your-username/Audio_Text.git
cd Audio_Text
python -m venv venv
venv\Scripts\activate
```

#### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

#### 3. (Optional) Test via CLI First
Verify your audio capture and Whisper engine in the terminal before starting the GUI:
```bash
python transcription_engine.py --model base.en
```
*Play some audio (e.g., a YouTube video or music). Transcriptions will start printing to the console.*

#### 4. Launch the Overlay GUI
```bash
python subtitle_overlay.py
```

---

## ⚙️ Configuration & Customization

The overlay settings are automatically persisted inside `config.json` in the application directory. You can adjust:
* **Audio Device Index:** Change the captured sound source (auto-detects default speaker by default).
* **Whisper Model:**
  * `tiny.en` — Extremely fast, low CPU usage, good accuracy.
  * `base.en` — Great balance of speed and accuracy (**default & recommended**).
  * `small.en` / `medium.en` — High accuracy, but slower on low-end CPUs.
* **Language:** Set custom language codes (e.g. `en`, `fr`, `de`, `es`) or `auto` for real-time translation/detection.
* **Compute Device:** `cpu` (default) or `cuda` (if you have an NVIDIA GPU).
* **Overlay Width:** Set default width (200px – 3840px).

Right-click the **`▓▓▓ SUBTITLES ▓▓▓`** handle bar at the top of the overlay to open the **Settings** window to adjust these parameters dynamically.

---

## 🛠️ Architecture Pipeline

```
          System Audio (Speakers/Headphones)
                          ↓
     SilentCaptureContext (ghost-volume unmute workaround)
                          ↓
      soundcard WASAPI Loopback Stream (64ms chunks @ 16kHz)
                          ↓
      Automatic Gain Control (AGC) [up to 50x peak normalization]
                          ↓
      Dual Voice Activity Detection (VAD)
        ├─ Speech Onset: Silero VAD (512-sample chunk-split)
        └─ Speech Silence: EnergyVAD
                          ↓ (Speech segments only, AGC-boosted)
      3.0-second Audio Segment Buffer → Queue
                          ↓
      faster-whisper (quantized int8/float16, beam_size=1, vad_filter=False)
                          ↓
      Whisper Hallucination & Punctuation Filter
                          ↓
      Tkinter Overlay Canvas (transparent, outline-drawn text)
```

---

## ⌨️ Global Shortcuts

* **`Ctrl + Alt + C`**: Copy all accumulated transcript text from the current session directly to the clipboard.
* **`Ctrl + Alt + Q`**: Stop the transcription engine, release audio hooks, and exit.

---

## 📦 Packaging Your Own Executable

If you modify the source code and want to compile a new `.exe`, run:
```bash
pip install pyinstaller
pyinstaller SubtitleOverlay.spec --noconfirm
```
The compiled binary will be placed inside `dist\SubtitleOverlay.exe`.

---

## ❓ Troubleshooting

| Issue | Cause | Solution |
|---|---|---|
| **Subtitles are delayed** | CPU bottleneck / large model | 1. Open Settings and switch to the `tiny.en` model.<br>2. If you have an NVIDIA GPU, set compute device to `cuda`. |
| **No transcription / Red Dot** | Device mismatch or engine startup crash | Right-click the handle bar → **Settings** and click to select your active audio device from the list of available devices. |
| **Can't drag or click handle bar** | Entire window click-through | Fixed. The application now uses colorkey transparency (`TRANSPARENT_COLOR`) to make background transparent while keeping the handle bar clickable. |
| **Sound is muted but capture fails** | Windows zeroes muted loopback streams | The app automatically uses a ghost-volume context manager (`pycaw`) to keep a 10% silent output stream alive. If it fails, unmute your speakers slightly. |
| **Whisper hallucinating sentences** | Silence noise triggering model | The hallucination filter automatically filters out standard Whisper loops (e.g., *"Thank you for watching"*). Adjust VAD thresholds in `transcription_engine.py` if needed. |
