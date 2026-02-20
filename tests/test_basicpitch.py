import os
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

from basic_pitch.inference import predict
from pathlib import Path

def main():
    print("BasicPitch imported OK")

    audio_path = Path("tests/data/silence.wav")
    print(f"Predicting MIDI for {audio_path}...")

    notes, _, _ = predict(audio_path)
    print("Notes:", notes)

if __name__ == "__main__":
    main()
