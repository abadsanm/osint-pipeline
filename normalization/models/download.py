"""
Download required models for the normalization pipeline.

Usage:
    python -m normalization.models.download

Downloads:
    - fastText lid.176.bin (~126MB) for language detection
    - spaCy en_core_web_lg (~560MB) for NER
"""

import os
import subprocess
import sys
import urllib.request

FASTTEXT_URL = "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin"
DEFAULT_MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "models")


def download_fasttext(model_dir: str = DEFAULT_MODEL_DIR):
    """Download fastText language detection model."""
    os.makedirs(model_dir, exist_ok=True)
    dest = os.path.join(model_dir, "lid.176.bin")

    if os.path.exists(dest):
        size_mb = os.path.getsize(dest) / (1024 * 1024)
        print(f"fastText model already exists at {dest} ({size_mb:.1f}MB)")
        return

    print(f"Downloading fastText lid.176.bin to {dest}...")
    print(f"  URL: {FASTTEXT_URL}")
    print("  This is ~126MB, may take a few minutes.")

    urllib.request.urlretrieve(FASTTEXT_URL, dest, _progress_hook)
    print(f"\nDone. Saved to {dest}")


def download_spacy(model_name: str = "en_core_web_lg"):
    """Download spaCy model."""
    print(f"Downloading spaCy model '{model_name}'...")
    subprocess.check_call([sys.executable, "-m", "spacy", "download", model_name])
    print(f"Done. spaCy model '{model_name}' installed.")


def _progress_hook(block_num, block_size, total_size):
    downloaded = block_num * block_size
    if total_size > 0:
        pct = min(100, downloaded * 100 / total_size)
        mb = downloaded / (1024 * 1024)
        total_mb = total_size / (1024 * 1024)
        print(f"\r  {mb:.1f}/{total_mb:.1f} MB ({pct:.0f}%)", end="", flush=True)


if __name__ == "__main__":
    download_fasttext()
    download_spacy()
    print("\nAll models downloaded.")
