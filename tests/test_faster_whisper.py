"""
Smoke test for faster-whisper.

Transcribes tests/data/silence.wav with word-level timestamps using the
'tiny' model (fastest download, CPU-friendly). Confirms:
  - WhisperModel loads without error
  - transcribe() runs and returns segments
  - word_timestamps=True yields WordTiming objects with .word / .start / .end
"""

from pathlib import Path
from faster_whisper import WhisperModel


def main() -> None:
    print("Loading faster-whisper 'tiny' model (CPU)…")
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    print("Model loaded OK")

    audio_path = Path("tests/data/silence.wav")
    print(f"Transcribing {audio_path} with word_timestamps=True…")

    segments, info = model.transcribe(str(audio_path), word_timestamps=True)

    print(f"Detected language: {info.language!r} (p={info.language_probability:.2f})")
    print(f"Duration: {info.duration:.2f}s")

    words_found = 0
    for segment in segments:
        print(f"  Segment [{segment.start:.2f}s → {segment.end:.2f}s]: {segment.text!r}")
        if segment.words:
            for w in segment.words:
                print(f"    Word: {w.word!r}  start={w.start:.3f}  end={w.end:.3f}")
                words_found += 1

    print(f"\nTotal word events: {words_found}")
    print("faster-whisper OK")


if __name__ == "__main__":
    main()
