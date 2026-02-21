import json
import os

print("=== Import test ===")
try:
    from models.stable_audio_open.inference.generation import (
        generate_diffusion_uncond,
        generate_diffusion_cond,
        generate_diffusion_cond_inpaint,
    )
    from models.stable_audio_open.inference.sampling import sample
    from models.stable_audio_open.models.factory import create_model_from_config
    print("SAO modules imported successfully.")
except Exception as e:
    print("Import error:", e)
    raise

print("\n=== Config load test ===")
base = "models/stable_audio_open/configs"

try:
    with open(os.path.join(base, "stable_audio_1_0.json")) as f:
        diffusion_cfg = json.load(f)
    with open(os.path.join(base, "stable_audio_1_0_vae.json")) as f:
        vae_cfg = json.load(f)
    print("Config files loaded successfully.")
except Exception as e:
    print("Config load error:", e)
    raise

print("\n=== Model construction test (no weights) ===")
try:
    diffusion_model = create_model_from_config(diffusion_cfg)
    vae_model = create_model_from_config(vae_cfg)
    print("Model objects constructed successfully.")
except Exception as e:
    print("Model construction error:", e)
    raise

print("\nAll SAO vendoring tests passed.")
