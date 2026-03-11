"""
bg_remover - Batch background removal for images using AI.

Processes all supported images in a directory and saves the results
with transparent backgrounds to an output folder.
"""

import argparse
import os
from pathlib import Path

from PIL import Image
from rembg import remove

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".bmp")


def remove_backgrounds(input_dir: Path, output_dir: Path) -> None:
    """Remove backgrounds from all images in input_dir and save to output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)

    images = [f for f in os.listdir(input_dir) if f.lower().endswith(IMAGE_EXTENSIONS)]

    if not images:
        print(f"No supported images found in {input_dir}")
        return

    print(f"Processing {len(images)} image(s)...")

    for filename in images:
        output_filename = Path(filename).stem + "_bg_removed.png"
        try:
            img = Image.open(input_dir / filename)
            result = remove(img)
            result.save(output_dir / output_filename)
            print(f"  [OK] {filename} -> {output_filename}")
        except Exception as e:
            print(f"  [FAIL] {filename}: {e}")

    print(f"\nDone. Results saved to: {output_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove backgrounds from all images in a directory."
    )
    parser.add_argument(
        "-i", "--input",
        type=Path,
        default=Path.home() / "Desktop",
        help="Input directory containing images (default: ~/Desktop)",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output directory for processed images (default: <input>/BackgroundRemoved)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_dir = args.input
    output_dir = args.output if args.output else input_dir / "BackgroundRemoved"

    if not input_dir.is_dir():
        print(f"Error: input directory '{input_dir}' does not exist.")
        raise SystemExit(1)

    remove_backgrounds(input_dir, output_dir)


if __name__ == "__main__":
    main()
