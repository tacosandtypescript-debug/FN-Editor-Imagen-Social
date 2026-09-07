from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / 'jobs'
im=Image.open(JOBS / 'power_hours_base.jpg').convert('RGBA')
d=ImageDraw.Draw(im,'RGBA'); fp=ROOT / 'barlow_font' / 'Barlow-BlackItalic.ttf'
head=ImageFont.truetype(fp,30); white=(255,255,255,255); purple=(139,61,255,255); orange=(255,122,0,255)
d.rounded_rectangle((55,1140,1025,1730),radius=28,fill=(10,5,38,235),outline=(139,61,255,220),width=3)
def center(t,y,f,c):
 b=d.textbbox((0,0),t,font=f); d.text(((1080-(b[2]-b[0]))//2,y),t,font=f,fill=c,stroke_width=1,stroke_fill=(0,0,0,230))
center('PRIMERAS 2 HORAS',1155,head,purple)
flags=Image.open(JOBS / 'flags.png').convert('RGBA')
# Chrome render: 5 primeras filas y 5 segundas filas.
first=flags.crop((0,0,720,215)).resize((850,254),Image.Resampling.LANCZOS)
second=flags.crop((0,215,720,430)).resize((850,254),Image.Resampling.LANCZOS)
im.alpha_composite(first,(115,1195))
center('SEGUNDAS 2 HORAS',1458,head,orange)
im.alpha_composite(second,(115,1490))
out=JOBS / 'power_hours_card_flags.jpg'; im.convert('RGB').save(out,quality=95,optimize=True); print(out)
