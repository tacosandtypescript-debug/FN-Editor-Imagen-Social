from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / 'jobs'
im=Image.open(JOBS / 'admin_codes_base.png').convert('RGBA')
d=ImageDraw.Draw(im,'RGBA'); fp=ROOT / 'barlow_font' / 'Barlow-BlackItalic.ttf'
font=ImageFont.truetype(fp,19); head=ImageFont.truetype(fp,25)
white=(255,255,255,255); gold=(255,209,102,255); purple=(139,61,255,255)
# Lista completa resumida solo en la separación visual, conservando todos los códigos y recompensas.
items=[
('WhereIsTheDustyTree','5.000 Sprite Dust'),('INVALIDCHEAT','2 Cheat Code Locator'),('ChatWhereDoYouFindTheKey','2 Extraction Accelerator'),('YourThoughtsAreMine','Void Master Geno + 5.000 Sprite Dust'),('InsertCoinToContinue','Te convierte en máquina arcade'),('BRB','Te convierte en inodoro'),('JONESYISGOLDEN','Gold Jonesy Sprite'),('OverrideXP','40.000 XP'),('FindItChat','2 Cheat Code Locator'),('H0p0nvc','2.000 Sprite Dust'),('Play4All','Cheat Master Jonesy Sprite'),('GatherAndCraft','Cheat Master Bush Sprite'),('8BitBlast','Cheat Master 8-Bit Sprite'),('O2Override','Cuddle Team Leader Wrixel + drop de suministros'),('PerfectOrder','4 Spicy Taco'),('SurviveTheNight','2 Cheat Code Locator'),('TakeYourHeart','2 Extraction Accelerator'),('LetsBlockAndRoll','Te convierte en bloque de Tetris'),('DontBlockMe','Te convierte en bloque de Tetris'),('IWannaFlyHigh','Cheat Master Tails Sprite'),('GottaGoFast','Cheat Master Sonic Sprite'),('Magilume','2.000 Sprite Dust'),('Born2Play','Cheat Master Adventure Sprite'),('BEMOREALIEN','Ready Loading Screen'),('Chispambo','2.000 Sprite Dust'),('Abgestaubt','2.000 Sprite Dust'),('Perlimpinpin','2.000 Sprite Dust'),('ReachYourImpossible','Block Party Loading Screen')]
# Encabezado y dos columnas, sin panel sólido.
b=d.textbbox((0,0),'LISTA COMPLETA',font=head); d.text(((1080-(b[2]-b[0]))//2,1110), 'LISTA COMPLETA',font=head,fill=purple,stroke_width=1,stroke_fill=(0,0,0,240))
cols=[items[:14],items[14:]]
for ci,col in enumerate(cols):
 x=55+ci*490; y=1145
 for code,reward in col:
  # Código en dorado; recompensa debajo en blanco, manteniendo cada entrada legible.
  d.text((x,y),code,font=font,fill=gold,stroke_width=1,stroke_fill=(0,0,0,230))
  d.text((x,y+20),reward,font=font,fill=white,stroke_width=1,stroke_fill=(0,0,0,230))
  y+=42
out=JOBS / 'admin_codes_card.png'; im.convert('RGB').save(out,format='PNG',compress_level=0); print(out)
