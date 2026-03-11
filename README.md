# bg_remover

A command-line tool that batch-removes backgrounds from images using AI, powered by [`rembg`](https://github.com/danielgatis/rembg) and [`Pillow`](https://python-pillow.org/).

## Features

- Batch-processes an entire folder of images in one command
- Supports PNG, JPG/JPEG, GIF, and BMP input formats
- Outputs transparent-background PNGs
- Configurable input and output directories via CLI flags

## Installation

```bash
# Clone the repository
git clone https://github.com/dafunk-boyang/bg_remover.git
cd bg_remover

# Install dependencies
pip install -r requirements.txt
```

> **Note:** The first run will automatically download the AI model (~170 MB) used by `rembg`.

## Usage

```bash
# Process all images on your Desktop (default)
python remover.py

# Specify a custom input directory
python remover.py --input /path/to/images

# Specify both input and output directories
python remover.py --input /path/to/images --output /path/to/results
```

### Options

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--input` | `-i` | Directory containing source images | `~/Desktop` |
| `--output` | `-o` | Directory for processed images | `<input>/BackgroundRemoved` |

### Example output

```
Processing 3 image(s)...
  [OK] photo.jpg -> photo_bg_removed.png
  [OK] portrait.png -> portrait_bg_removed.png
  [OK] product.jpeg -> product_bg_removed.png

Done. Results saved to: /home/user/Desktop/BackgroundRemoved
```

## Dependencies

- [rembg](https://github.com/danielgatis/rembg) — AI-based background removal
- [Pillow](https://python-pillow.org/) — Image I/O and processing

## License

MIT
