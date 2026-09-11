from PIL import Image, ImageDraw, ImageFont, ImageFilter
from pathlib import Path
W,H=1080,1920
ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / 'examples' / 'historical'
src=Image.open(JOBS / 'admin_codes.jpg').convert('RGB')
# Fondo blur cover
scale=max(W/src.width,H/src.height)
bg=src.resize((round(src.width*scale),round(src.height*scale)),Image.Resampling.LANCZOS)
l=(bg.width-W)//2; t=(bg.height-H)//2
bg=bg.crop((l,t,l+W,t+H)).filter(ImageFilter.GaussianBlur(28)).convert('RGBA')
d=ImageDraw.Draw(bg,'RGBA')
font_path=ROOT / 'barlow_font' / 'Barlow-BlackItalic.ttf'
white=(255,255,255,255); gold=(255,209,102,255); purple=(139,61,255,255); orange=(255,122,0,255)
def font(size): return ImageFont.truetype(font_path,size)
def centered(text,y,f,fill=white,stroke=6):
    box=d.textbbox((0,0),text,font=f,stroke_width=stroke)
    d.text(((W-(box[2]-box[0]))//2,y),text,font=f,fill=fill,stroke_width=stroke,stroke_fill=(0,0,0,235))
# Título: caja x=150..930, y=150..360
centered('TODOS LOS CÓDIGOS',150,font(76),white)
# segmentos coloreados, segunda línea centrada
f=font(76); parts=[('DE ',white),('ADMIN',orange),(' EN FORTNITE',white)]
widths=[d.textbbox((0,0),s,font=f,stroke_width=6)[2] for s,_ in parts]
x=(W-sum(widths))//2
for (s,c),ww in zip(parts,widths):
    d.text((x,245),s,font=f,fill=c,stroke_width=6,stroke_fill=(0,0,0,235)); x+=ww
# Imagen/recuadro principal exacto x=90,y=390,w=900,h=430, con borde redondeado y glow
panel=src.resize((900,430),Image.Resampling.LANCZOS).convert('RGBA')
radius=32
mask=Image.new('L',(900,430),0); md=ImageDraw.Draw(mask); md.rounded_rectangle((0,0,899,429),radius=radius,fill=255)
# glow y sombra exterior suave siguiendo la silueta redondeada
glow_mask=mask.filter(ImageFilter.GaussianBlur(26))
glow=Image.new('RGBA',(960,490),(139,61,255,0)); glow.putalpha(glow_mask.resize((960,490),Image.Resampling.BILINEAR))
bg.alpha_composite(glow,(60,370))
shadow_mask=mask.filter(ImageFilter.GaussianBlur(18))
shadow=Image.new('RGBA',(940,470),(0,0,0,0)); shadow.putalpha(shadow_mask.resize((940,470),Image.Resampling.BILINEAR))
bg.alpha_composite(shadow,(72,402))
# borde suave y contenido sin deformar
rounded=Image.new('RGBA',(900,430),(0,0,0,0)); rounded.paste(panel,(0,0),mask)
bg.alpha_composite(rounded,(90,390))
d.rounded_rectangle((90,390,989,819),radius=radius,outline=(255,255,255,95),width=3)
# lista exacta conservada
items=[('WhereIsTheDustyTree','5.000 Sprite Dust'),('INVALIDCHEAT','2 Cheat Code Locator'),('ChatWhereDoYouFindTheKey','2 Extraction Accelerator'),('YourThoughtsAreMine','Void Master Geno + 5.000 Sprite Dust'),('InsertCoinToContinue','Te convierte en máquina arcade'),('BRB','Te convierte en inodoro'),('JONESYISGOLDEN','Gold Jonesy Sprite'),('OverrideXP','40.000 XP'),('FindItChat','2 Cheat Code Locator'),('H0p0nvc','2.000 Sprite Dust'),('Play4All','Cheat Master Jonesy Sprite'),('GatherAndCraft','Cheat Master Bush Sprite'),('8BitBlast','Cheat Master 8-Bit Sprite'),('O2Override','Cuddle Team Leader Wrixel + drop de suministros'),('PerfectOrder','4 Spicy Taco'),('SurviveTheNight','2 Cheat Code Locator'),('TakeYourHeart','2 Extraction Accelerator'),('LetsBlockAndRoll','Te convierte en bloque de Tetris'),('DontBlockMe','Te convierte en bloque de Tetris'),('IWannaFlyHigh','Cheat Master Tails Sprite'),('GottaGoFast','Cheat Master Sonic Sprite'),('Magilume','2.000 Sprite Dust'),('Born2Play','Cheat Master Adventure Sprite'),('BEMOREALIEN','Ready Loading Screen'),('Chispambo','2.000 Sprite Dust'),('Abgestaubt','2.000 Sprite Dust'),('Perlimpinpin','2.000 Sprite Dust'),('ReachYourImpossible','Block Party Loading Screen')]
# Subtítulo en caja x=340,y=860,w=400,h=60
centered('LISTA COMPLETA',860,font(38),purple,2)
# columnas x=110,y=950,w=390,h=560 y x=580,y=950,w=390,h=560
codef=font(19); rewardf=font(16)
for col,x in enumerate((110,580)):
    y=950
    for code,reward in items[col*14:(col+1)*14]:
        # Código y recompensa en dos líneas, con una separación ligeramente mayor.
        cf=codef
        rf=rewardf
        while d.textbbox((0,0),code,font=cf,stroke_width=1)[2] > 390 and cf.size>13: cf=font(cf.size-1)
        while d.textbbox((0,0),reward,font=rf,stroke_width=1)[2] > 390 and rf.size>11: rf=font(rf.size-1)
        d.text((x,y),code,font=cf,fill=gold,stroke_width=1,stroke_fill=(0,0,0,235))
        d.text((x,y+27),reward,font=rf,fill=white,stroke_width=1,stroke_fill=(0,0,0,235))
        y+=40
# Código de creador en caja x250,y1670,w580,h70
wf=font(60); wm='CÓDIGO: KHETZALGG'; bb=d.textbbox((0,0),wm,font=wf,stroke_width=2)
d.text(((W-(bb[2]-bb[0]))//2,1670),wm,font=wf,fill=(255,255,255,190),stroke_width=2,stroke_fill=(0,0,0,150))
out=JOBS / 'admin_codes_card.png'
bg.convert('RGBA').save(out,format='PNG',compress_level=0)
print(out)
