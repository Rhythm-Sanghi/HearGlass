import soundcard as sc

speaker = sc.default_speaker()
print(f"Default speaker: {speaker.name}")

mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
print(f"Loopback mic: {mic.name}")

with mic.recorder(samplerate=16000, channels=1) as recorder:
    data = recorder.record(numframes=100)
    print(data.shape, data.dtype)
