from rembg import remove
from PIL import Image
import os
from pathlib import Path



folder_path = Path.home() / "Desktop"
image_extensions = (".png", ".jpg", ".jpeg", ".gif", ".bmp")
output_folder = folder_path / "BackgroundRemoved"
output_folder.mkdir(exist_ok=True)

for filename in os.listdir(folder_path):
    try:
        if filename.lower().endswith(image_extensions):
            output_file = f'{filename}_BG_removed.png'
            inp = Image.open(folder_path / filename)
            output = remove(inp)
            output.save(output_folder / output_file)
            print(f'{filename} BG removed \nNamed: {output_file}')
    except Exception as e:
        print(e)