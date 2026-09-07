from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

src = Path('/home/isaac/editimg_work/jobs/power_hours_base.jpg')
out = Path('/home/isaac/editimg_work/jobs/power_hours_card_complete.jpg')
im = Image.open(src).convert('RGBA')
d = ImageDraw.Draw(im, 'RGBA')
font_path = '/home/isaac/.local/share/fonts/Barlow-BlackItalic.ttf'
font = ImageFont.truetype(font_path, 25)
head = ImageFont.truetype(font_path, 30)
small = ImageFont.truetype(font_path, 22)
# Panel seguro para conservar horarios y banderas completos.
x1, y1, x2, y2 = 55, 1230, 1025, 1720
d.rounded_rectangle((x1, y1, x2, y2), radius=28, fill=(10, 5, 38, 225), outline=(139, 61, 255, 210), width=3)
white=(255,255,255,255); purple=(139,61,255,255); orange=(255,122,0,255); magenta=(232,61,255,255); gold=(255,209,102,255)
def center(text, y, f, fill=white):
    box=d.textbbox((0,0),text,font=f,stroke_width=1)
    d.text(((1080-(box[2]-box[0]))//2,y),text,font=f,fill=fill,stroke_width=1,stroke_fill=(0,0,0,230))
center('PRIMERAS 2 HORAS', 1250, head, purple)
rows1=[('[SV HN MX CR]','12:00'),('[CO EC PE PA PY]','13:00'),('[CL BO VE]','14:00'),('[AR BR UY]','15:00'),('[ES]','20:00')]
rows2=[('[SV HN MX CR]','19:00'),('[CO EC PE PA PY]','20:00'),('[CL BO VE]','21:00'),('[AR BR UY]','22:00'),('[ES]','03:00 (DOMINGO)')]
# Use text labels as well so flags remain understandable on fonts without emoji color support.
def draw_rows(rows, start_y):
    for i,(flags,time) in enumerate(rows):
        text=f'{flags}   {time}'
        center(text, start_y+i*38, font, white)
draw_rows(rows1,1290)
center('SEGUNDAS 2 HORAS', 1490, head, orange)
draw_rows(rows2,1530)
im.convert('RGB').save(out, quality=95, optimize=True)
print(out)
