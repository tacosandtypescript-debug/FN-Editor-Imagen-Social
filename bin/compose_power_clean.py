from PIL import Image
im=Image.open('/home/isaac/editimg_work/jobs/power_hours_base.jpg').convert('RGBA')
s=Image.open('/home/isaac/editimg_work/jobs/schedule.png').convert('RGBA')
# Escala conservadora, sin deformar; solo texto blanco y banderas.
s.thumbnail((1040,340),Image.Resampling.LANCZOS)
im.alpha_composite(s,((1080-s.width)//2,1320))
out='/home/isaac/editimg_work/jobs/power_hours_card_clean.jpg'
im.convert('RGB').save(out,quality=95,optimize=True)
print(out,im.size,s.size)
