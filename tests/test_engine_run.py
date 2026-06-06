from transcription_engine import TranscriptionEngine
import time

def on_t(t, partial):
    print('GOT TRANSCRIPT:', t)

e = TranscriptionEngine(on_transcript=on_t)
e.start()
print('Engine started, waiting for 5 seconds...')
time.sleep(5)
print('Done.')
