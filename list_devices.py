"""
list_devices.py — Audio Device Enumerator (soundcard)
=======================================================
Run this script to find your WASAPI loopback device index.
The indices printed here match exactly what the transcription engine
and subtitle_overlay.py expect via --device INDEX.

Usage:
    python list_devices.py

Copy the index number next to your loopback / speaker device and pass it:
    python subtitle_overlay.py --device INDEX
    python transcription_engine.py --device INDEX

NOTE: These are soundcard library indices, NOT PyAudio indices.
"""

import sys

try:
    import soundcard as sc
except ImportError:
    print("[ERROR] soundcard is not installed. Run: pip install soundcard")
    sys.exit(1)


def list_audio_devices() -> None:
    print("\n" + "=" * 72)
    print("  AUDIO DEVICE ENUMERATION  (soundcard library)")
    print("=" * 72)

    # ── All speakers / playback devices ──────────────────────────────────────
    speakers = sc.all_speakers()
    print(f"\n{'IDX':>4}  {'NAME':<60}  DEFAULT")
    print("  " + "-" * 70)

    default_speaker = sc.default_speaker()
    for idx, spk in enumerate(speakers):
        is_default = "  ◄ default" if spk.name == default_speaker.name else ""
        print(f"{idx:>4}  {spk.name:<60}{is_default}")

    # ── All microphones / loopback devices ───────────────────────────────────
    print(f"\n{'IDX':>4}  {'NAME':<60}  LOOPBACK?")
    print("  " + "-" * 70)

    all_mics = sc.all_microphones(include_loopback=True)
    loopback_candidates: list[tuple[int, str]] = []

    for idx, mic in enumerate(all_mics):
        name_lower = mic.name.lower()

        # Heuristic: loopback mics contain the default speaker's name or
        # common Windows loopback keywords.
        is_loopback = (
            default_speaker.name.lower() in name_lower
            or "loopback" in name_lower
            or "stereo mix" in name_lower
            or "what u hear" in name_lower
            or "wave out" in name_lower
        )

        loopback_tag = "  *** LOOPBACK ***" if is_loopback else ""
        print(f"{idx:>4}  {mic.name:<60}{loopback_tag}")

        if is_loopback:
            loopback_candidates.append((idx, mic.name))

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    if loopback_candidates:
        print("  RECOMMENDED LOOPBACK DEVICES:")
        for idx, name in loopback_candidates:
            print(f"    Index {idx:>3}  {name}")
        best_idx = loopback_candidates[0][0]
        print(f"\n  → Use --device {best_idx} when launching subtitle_overlay.py")
        print(f"    or leave --device unset to let the engine auto-detect.")
    else:
        print("  No loopback candidates found automatically.")
        print("  The engine will still try to auto-detect on startup.")
        print()
        print("  Troubleshooting steps:")
        print("    1. Right-click the speaker icon in the taskbar → Sound settings")
        print("    2. Go to 'Recording' tab → Right-click → 'Show Disabled Devices'")
        print("    3. Enable 'Stereo Mix' and re-run this script.")
        print("    4. Alternatively, install VB-Cable (virtual audio cable).")
    print("=" * 72 + "\n")


if __name__ == "__main__":
    list_audio_devices()
