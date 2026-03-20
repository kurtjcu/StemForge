#!/usr/bin/env bash
# Download and install LarsNet pretrained checkpoints to the StemForge model cache.
#
# Weights: 5 U-Net checkpoints (~562 MB total, zipped)
# Source: polimi-ispl/larsnet (https://github.com/polimi-ispl/larsnet)
# License: CC BY-NC 4.0 — non-commercial use only
# Google Drive file ID: 1U8-5924B1ii1cjv9p0MTPzayb00P4qoL
#
# Usage:
#   bash scripts/download_larsnet_weights.sh
#   MODEL_LOCATION=/custom/cache bash scripts/download_larsnet_weights.sh

set -euo pipefail

CACHE_DIR="${MODEL_LOCATION:-$HOME/.cache/stemforge}/larsnet"
mkdir -p "$CACHE_DIR"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "Downloading LarsNet pretrained models (562 MB)..."
echo "License: CC BY-NC 4.0 — non-commercial use only."
echo ""

gdown --id 1U8-5924B1ii1cjv9p0MTPzayb00P4qoL --output "$TMP/larsnet_checkpoints.zip"

echo "Extracting..."
unzip -q "$TMP/larsnet_checkpoints.zip" -d "$TMP/extracted"

# Reorganize into per-stem subdirectories expected by LarsNetBackend
for stem in kick snare toms hihat cymbals; do
    mkdir -p "$CACHE_DIR/$stem"
    find "$TMP/extracted" -name "pretrained_${stem}_unet.pth" -exec cp {} "$CACHE_DIR/$stem/" \;
done

# Verify all 5 checkpoints were extracted
MISSING=0
for stem in kick snare toms hihat cymbals; do
    if [ ! -f "$CACHE_DIR/$stem/pretrained_${stem}_unet.pth" ]; then
        echo "WARNING: Missing checkpoint for $stem"
        MISSING=$((MISSING + 1))
    fi
done

if [ "$MISSING" -gt 0 ]; then
    echo "ERROR: $MISSING checkpoint(s) missing. Archive structure may have changed."
    echo "Check: https://github.com/polimi-ispl/larsnet for updated download instructions."
    exit 1
fi

echo ""
echo "LarsNet weights installed to: $CACHE_DIR"
echo "Contents:"
ls -R "$CACHE_DIR"/*.pth 2>/dev/null || ls -R "$CACHE_DIR"
