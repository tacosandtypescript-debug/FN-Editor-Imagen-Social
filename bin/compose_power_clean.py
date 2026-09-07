from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / 'jobs'
im=Image.open(JOBS / 'power_hours_base.jpg').convert('RGBA')
s=Image.open(JOBS / 'schedule.png').convert('RGBA')
# Escala conservadora, sin deformar; solo texto blanco y banderas.
s.thumbnail((1040,340),Image.Resampling.LANCZOS)
im.alpha_composite(s,((1080-s.width)//2,1320))
out=JOBS / 'power_hours_card_clean.jpg'
im.convert('RGB').save(out,quality=95,optimize=True)
print(out,im.size,s.size)
