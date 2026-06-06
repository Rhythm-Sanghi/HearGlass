import sys
import time
sys.path.append('.')
import soundcard as sc
import numpy as np
from transcription_engine import SileroVAD

vad = SileroVAD()
print('VAD available:', vad.available)

mic = sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True)
print('Mic:', mic.name)

with mic.recorder(samplerate=16000, channels=1) as recorder:
    print('Listening... please play audio!')
    for i in range(10):
        data = recorder.record(numframes=512)
        data = data.mean(axis=1) # FIX
        pcm = (data * 32767).astype(np.int16)
        is_speech = vad.is_speech(pcm)
        print(f'[{i}] Max amplitude: {np.abs(pcm).max()}, Speech: {is_speech}')
