from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FONTDIR = Path('/home/isaac/.local/share/fonts')
OUTDIR = Path('/home/isaac/editimg_work/jobs')
W, H = 1080, 1920
BG = (27, 32, 42)
WHITE = (255, 255, 255)
CYAN = (0, 229, 255)
GOLD = (255, 215, 0)

styles = [
    ('Barlow-Black.ttf', 'BLACK'),
    ('Barlow-BlackItalic.ttf', 'BLACK ITALIC'),
    ('Barlow-ExtraBold.ttf', 'EXTRABOLD'),
    ('Barlow-ExtraBoldItalic.ttf', 'EXTRABOLD ITALIC'),
    ('Barlow-Bold.ttf', 'BOLD'),
    ('Barlow-BoldItalic.ttf', 'BOLD ITALIC'),
    ('Barlow-SemiBold.ttf', 'SEMIBOLD'),
    ('Barlow-SemiBoldItalic.ttf', 'SEMIBOLD ITALIC'),
    ('Barlow-Medium.ttf', 'MEDIUM'),
    ('Barlow-MediumItalic.ttf', 'MEDIUM ITALIC'),
    ('Barlow-Regular.ttf', 'REGULAR'),
    ('Barlow-Italic.ttf', 'ITALIC'),
    ('Barlow-Light.ttf', 'LIGHT'),
    ('Barlow-LightItalic.ttf', 'LIGHT ITALIC'),
    ('Barlow-ExtraLight.ttf', 'EXTRALIGHT'),
    ('Barlow-ExtraLightItalic.ttf', 'EXTRALIGHT ITALIC'),
    ('Barlow-Thin.ttf', 'THIN'),
    ('Barlow-ThinItalic.ttf', 'THIN ITALIC'),
]

def F(name, size):
    return ImageFont.truetype(str(FONTDIR / name), size)

def make_sheet(title, entries, out):
    im = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(im)
    d.text((60, 45), title, font=F('Barlow-Bold.ttf', 52), fill=CYAN)
    d.text((60, 105), 'EJEMPLO: NOVEDADES DE LA ACTUALIZACIÓN', font=F('Barlow-Regular.ttf', 27), fill=(190, 200, 215))
    row_h = 270
    y = 180
    for filename, label in entries:
        d.rounded_rectangle((45, y, W-45, y+235), radius=24, fill=(38, 45, 58), outline=(70, 82, 100), width=2)
        d.text((75, y+25), label, font=F('Barlow-SemiBold.ttf', 27), fill=GOLD)
        sample = 'NOVEDADES DE LA ACTUALIZACIÓN'
        font = F(filename, 55)
        # fit conservatively
        while d.textbbox((0,0), sample, font=font)[2] > 900 and font.size > 25:
            font = F(filename, font.size-1)
        d.text((75, y+85), sample, font=font, fill=WHITE)
        d.text((75, y+170), 'Barlow · titular de noticia', font=F('Barlow-Regular.ttf', 24), fill=(160, 175, 195))
        y += row_h
    im.save(OUTDIR / out, quality=95)

make_sheet('BARLOW · PESOS FUERTES', styles[:8], 'barlow_fuertes.jpg')
make_sheet('BARLOW · PESOS NORMALES Y LIGEROS', styles[8:16], 'barlow_ligeras.jpg')
make_sheet('BARLOW · MÁS DELGADAS', styles[16:], 'barlow_delgadas.jpg')
print('generadas:', 'barlow_fuertes.jpg', 'barlow_ligeras.jpg', 'barlow_delgadas.jpg')
