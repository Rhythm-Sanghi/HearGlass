"""
silent_capture.py — Windows "Silent Loopback" Audio Endpoint Manager
=====================================================================
Solves the problem: WASAPI loopback capture returns silence when the
system speakers are muted (Windows zeroes the render stream at the
audio endpoint before the loopback tap sees it).

Strategy
--------
Before starting audio capture we:
  1. Save the current endpoint master mute state + volume level.
  2. *Unmute* the endpoint and set volume to a tiny ghost level (1%)
     so the audio render stream carries real data through the loopback
     buffer — but the user hears nothing.
  3. On stop/error we restore the original state exactly.

Usage (as a context manager)
-----------------------------
    with SilentCaptureContext() as ctx:
        # do loopback recording here — works even if speakers were muted
        ...
    # original volume/mute state is restored automatically

Usage (manual)
--------------
    ctx = SilentCaptureContext()
    ctx.enable()
    # ... recording ...
    ctx.disable()

Non-Windows
-----------
On non-Windows platforms SilentCaptureContext is a no-op context manager
so the rest of the code is fully portable.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger("subtitle.capture")

_IS_WINDOWS = sys.platform == "win32"

# Ghost volume level used while capturing — just enough to keep the
# render stream active and above the noise gate of most drivers.
# 10% (-20 dB) is quiet but ensures a robust signal for the loopback buffer.
_GHOST_VOLUME: float = 0.10


if _IS_WINDOWS:
    try:
        from pycaw.pycaw import AudioUtilities
        _PYCAW_AVAILABLE = True
    except ImportError:
        _PYCAW_AVAILABLE = False
        log.warning(
            "pycaw not installed. Loopback capture will fail when speakers are muted. "
            "Fix: pip install pycaw"
        )


class SilentCaptureContext:
    """
    Context manager / manual controller that keeps the Windows audio
    render endpoint alive (unmuted, ghost volume) during loopback capture.

    On non-Windows or when pycaw is unavailable, this is a safe no-op.
    """

    def __init__(self, ghost_volume: float = _GHOST_VOLUME) -> None:
        self._ghost_volume = ghost_volume
        self._endpoint = None          # IAudioEndpointVolume COM object
        self._original_mute: bool | None = None
        self._original_volume: float | None = None
        self._active = False

    # ── Context manager protocol ──────────────────────────────────────────────

    def __enter__(self) -> "SilentCaptureContext":
        self.enable()
        return self

    def __exit__(self, *_) -> None:
        self.disable()

    # ── Public API ────────────────────────────────────────────────────────────

    def enable(self) -> None:
        """Unmute endpoint and set ghost volume; save original state."""
        if not _IS_WINDOWS or not _PYCAW_AVAILABLE:
            return
        try:
            device = AudioUtilities.GetSpeakers()
            volume = device.EndpointVolume

            # Save current state
            self._original_mute = bool(volume.GetMute())
            self._original_volume = float(volume.GetMasterVolumeLevelScalar())
            self._endpoint = volume

            # Logic: If the system is muted or volume is near-zero, we must
            # "activate" it by unmuting and setting a minimum ghost volume.
            # If it's already unmuted and has volume, we leave it alone.
            if self._original_mute or self._original_volume < 0.001:
                # To avoid a "blast" of sound if volume was high but muted:
                # 1. Set volume to ghost level FIRST
                volume.SetMasterVolumeLevelScalar(self._ghost_volume, None)
                # 2. Then unmute
                volume.SetMute(False, None)
                
                self._active = True
                log.info(
                    "System was muted/zero. Activated ghost mode at %.0f%% volume.",
                    self._ghost_volume * 100,
                )
            else:
                # System is already active, no need to touch it.
                self._active = False
                log.info(
                    "System is already active (%.0f%%). Leaving as-is.",
                    self._original_volume * 100,
                )

        except Exception as exc:
            log.warning("Could not enable ghost mode: %s", exc)
            self._active = False

    def disable(self) -> None:
        """Restore original mute state and volume level."""
        if not self._active or self._endpoint is None:
            return
        try:
            # Restore volume first, then mute — order matters to avoid a
            # brief audible blip if volume was originally high.
            if self._original_volume is not None:
                self._endpoint.SetMasterVolumeLevelScalar(self._original_volume, None)
            if self._original_mute is not None:
                self._endpoint.SetMute(self._original_mute, None)

            log.info(
                "Restored endpoint: mute=%s, vol=%.1f%%",
                self._original_mute,
                (self._original_volume or 0) * 100,
            )
        except Exception as exc:
            log.warning("Could not restore endpoint state: %s", exc)
        finally:
            self._active = False
            self._endpoint = None
            self._original_mute = None
            self._original_volume = None

    # ── Convenience ───────────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self._active
