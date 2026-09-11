from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / 'examples' / 'historical'
im=Image.open(JOBS / 'power_hours_base.jpg').convert('RGBA')
d=ImageDraw.Draw(im,'RGBA'); fp=ROOT / 'barlow_font' / 'Barlow-BlackItalic.ttf'
head=ImageFont.truetype(fp,28)
# Solo texto sobre la imagen; no se añade ningún cuadro de fondo.
def center_at(t,x,y,w,f,c):
 b=d.textbbox((0,0),t,font=f); d.text((x+(w-(b[2]-b[0]))//2,y),t,font=f,fill=c,stroke_width=1,stroke_fill=(0,0,0,230))
flags=Image.open(JOBS / 'flags.png').convert('RGBA')
# Each column gets its own five-row crop, preserving real color flags.
first=flags.crop((0,0,1200,290)).resize((450,218),Image.Resampling.LANCZOS)
second=flags.crop((0,290,1200,620)).resize((450,218),Image.Resampling.LANCZOS)
purple=(139,61,255,255); orange=(255,122,0,255)
center_at('PRIMERAS 2 HORAS',55,1250,450,head,purple)
center_at('SEGUNDAS 2 HORAS',575,1250,450,head,orange)
im.alpha_composite(first,(60,1300)); im.alpha_composite(second,(580,1300))
out=JOBS / 'power_hours_card_flags_fixed.jpg'; im.convert('RGB').save(out,quality=95,optimize=True); print(out)
