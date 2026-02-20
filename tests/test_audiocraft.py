from audiocraft.models import MusicGen

model = MusicGen.get_pretrained("facebook/musicgen-small")
model.set_generation_params(duration=1)

wav = model.generate_unconditional(1)
print("Generated waveform shape:", wav[0].shape)
