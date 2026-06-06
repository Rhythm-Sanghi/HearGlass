import sys
sys.path.append('.')
import soundcard as sc
import numpy as np
from transcription_engine import SileroVAD

vad = SileroVAD()
print('VAD available:', vad.available)

mic = sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True)
print('Mic:', mic.name)

with mic.recorder(samplerate=16000, channels=1) as recorder:
    print('Recording 10 chunks of 480 samples...')
    for _ in range(20):
        data = recorder.record(numframes=480)
        pcm = (data * 32767).astype(np.int16)
        is_speech = vad.is_speech(pcm)
        print(f'Max amplitude: {np.abs(pcm).max()}, Speech: {is_speech}')
