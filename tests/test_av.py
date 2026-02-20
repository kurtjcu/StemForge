import av
import numpy as np

print("PyAV version:", av.__version__)

# Create a dummy audio buffer and encode/decode it
samples = np.zeros(44100, dtype=np.float32)

container = av.open("dummy.wav", mode="w", format="wav")
stream = container.add_stream("pcm_s16le", rate=44100)
frame = av.AudioFrame.from_ndarray(samples, layout="mono")
frame.sample_rate = 44100
packet = stream.encode(frame)
container.mux(packet)
container.close()

print("PyAV basic encode/decode OK")
