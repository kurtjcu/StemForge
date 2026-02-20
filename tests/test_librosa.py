import librosa
import numpy as np

audio = np.zeros(44100)
spec = librosa.stft(audio)

print("STFT shape:", spec.shape)
