"""
End-to-end smoke test for VocalMidiPipeline.

Uses the ACE-Step pair:
  /home/tsondo/OneDrive/Documents/Ace/7cb32358-09fd-51b8-68fe-f99d80af37ad.flac
  /home/tsondo/OneDrive/Documents/Ace/7cb32358-09fd-51b8-68fe-f99d80af37ad.json

Output MIDI is written to tests/data/test_output.mid.

Expected runtime (CPU):
  Demucs separation  ~3–5 min  (180 s audio, htdemucs)
  Whisper tiny       ~30–60 s
  BasicPitch         ~60–90 s
  Quantise + assemble < 1 s
"""

import pathlib
import time

ACE_DIR   = pathlib.Path("/home/tsondo/OneDrive/Documents/Ace")
AUDIO     = ACE_DIR / "7cb32358-09fd-51b8-68fe-f99d80af37ad.flac"
JSON      = ACE_DIR / "7cb32358-09fd-51b8-68fe-f99d80af37ad.json"
OUT_MIDI  = pathlib.Path("tests/data/test_output.mid")


def progress(pct: float, stage: str) -> None:
    print(f"  [{pct:5.1f}%] {stage}")


def main() -> None:
    from pipelines.vocal_midi_pipeline import VocalMidiConfig, VocalMidiPipeline

    print(f"Audio : {AUDIO}")
    print(f"JSON  : {JSON}")
    print(f"Output: {OUT_MIDI}")
    print()

    pipeline = VocalMidiPipeline()
    pipeline.set_progress_callback(progress)

    pipeline.configure(
        VocalMidiConfig(
            json_path=JSON,
            output_path=OUT_MIDI,
            whisper_model_size="tiny",   # fast for smoke test; use "base" in production
            whisper_device="cpu",
            whisper_compute_type="int8",
            demucs_model="htdemucs",
        )
    )
    print("Loading models…")
    t0 = time.perf_counter()
    pipeline.load_model()
    print(f"Models loaded in {time.perf_counter() - t0:.1f}s\n")

    print("Running pipeline…")
    t1 = time.perf_counter()
    result = pipeline.run(AUDIO)
    elapsed = time.perf_counter() - t1

    print(f"\nPipeline completed in {elapsed:.1f}s")
    print(f"  MIDI path     : {result.midi_path}")
    print(f"  BPM           : {result.bpm}")
    print(f"  Key           : {result.key}")
    print(f"  Notes         : {result.note_count}")
    print(f"  Lyric events  : {result.word_count}")
    print(f"  Duration      : {result.duration_seconds:.2f}s")
    print(f"  File size     : {result.midi_path.stat().st_size:,} bytes")

    pipeline.clear()
    print("\nDone.")


if __name__ == "__main__":
    main()
