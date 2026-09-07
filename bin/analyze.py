import argparse
from PIL import Image
from collections import Counter

ap = argparse.ArgumentParser(description='Analiza el color dominante de una imagen.')
ap.add_argument('image', help='ruta de la imagen a analizar')
a = ap.parse_args()
im = Image.open(a.image).convert('RGB')
W, H = im.size
px = im.load()
cnt = Counter()
for y in range(0, H, 6):
    for x in range(0, W, 6):
        cnt[px[x, y]] += 1
bg = cnt.most_common(1)[0][0]
print('dominant bg', bg, 'W,H', W, H)


def diff(c):
    return abs(c[0] - bg[0]) + abs(c[1] - bg[1]) + abs(c[2] - bg[2])


for y in range(H - 180, H - 15, 8):
    run = 0
    maxrun = 0
    segs = []
    prev = False
    for x in range(0, W):
        d = diff(px[x, y]) > 40
        if d and not prev:
            start = x
        if d:
            run += 1
        if (not d or x == W - 1) and prev:
            segs.append((start, x))
        prev = d
    segs = [s for s in segs if s[1] - s[0] > 8]
    print(y, 'segments:', segs[:12])
