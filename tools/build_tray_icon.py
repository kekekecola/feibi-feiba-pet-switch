"""Export a multi-resolution tray ICO from the existing Feibi sprite artwork."""
from pathlib import Path
from PIL import Image
root = Path(__file__).resolve().parents[1]
with Image.open(root / 'pets/feibi-jiubi/spritesheet.png') as sheet:
    head = sheet.convert('RGBA').crop((16, 8, 176, 145))
    head = head.crop(head.getchannel('A').getbbox())
    factor = min(252 / head.width, 252 / head.height)
    head = head.resize((round(head.width * factor), round(head.height * factor)), Image.Resampling.LANCZOS)
    canvas = Image.new('RGBA', (256, 256))
    canvas.alpha_composite(head, ((256-head.width)//2, (256-head.height)//2))
    canvas.save(root / 'app/assets/feibi-tray.ico', sizes=[(n,n) for n in (16,20,24,32,40,48,64,128,256)])
