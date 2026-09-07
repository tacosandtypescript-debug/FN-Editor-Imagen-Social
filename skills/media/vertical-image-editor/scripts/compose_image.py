#!/usr/bin/env python3
"""Compone una imagen vertical 9:16 (1080x1920) con fondo cover blur, uno o
varios sujetos (collage 2-4 imagenes), esquinas redondeadas, sombra exterior de
dos capas y texto superior/inferior segmentado con palabras de color.

Uso:
  compose_image.py imagen1.jpg [imagen2.jpg ...] salida.jpg \
      --top 'TEXTO {CLAVE|FFFFFF}' --bottom '...' [--preset preset.json] [--style auto]

El ULTIMO argumento posicional es la salida; todos los anteriores son imagenes
de entrada (1 = tarjeta simple, 2-4 = collage).

Estilos de collage (--style):
  auto         -> elige segun N y proporcion de la primera imagen (defecto)
  grid         -> cuadricula uniforme (legacy, contain exacto)
  bento        -> bloques cuadrados y rectangulares de distintos tamanos, con
                  huecos de diseno (muy ordenado/moderno)
  mosaico      -> celdas que encajan sin espacios, filas de distinta altura
  puzzle       -> piezas rectangulares desiguales que encajan sin huecos
  jerarquico   -> una imagen grande protagonista + secundarias mas pequenas
  asimetrico   -> una imagen grande y varias pequenas alrededor

El calculo de texto/posiciones es el mismo para todos los estilos; cada celda
rellena su rectangulo con cover centrado (nunca deforma; recorta los bordes
justos). La tarjeta simple (1 imagen) y --style grid conservan contain.
"""
import argparse, json, math, re, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

SEG = re.compile(r"\{([^{}|]+)\|([0-9A-Fa-f]{6})\}")

# ── Layouts de collage ──────────────────────────────────────────────────────
# Cada layout: A_h (alto total en unidades del ancho) + lista de celdas
# (x, y, w, h) fracciones de un area de ancho 1.0. Todas cubren sin deformar.
def _lay():
    L = {}
    # Jerarquico: protagonista grande arriba (anchas) o izquierda (N=2).
    L[("jerarquico", 2)] = (1.0, [(0, 0, 1.0, 0.62), (0, 0.66, 1.0, 0.34)])
    L[("jerarquico", 3)] = (1.0, [(0, 0, 1.0, 0.5), (0, 0.54, 0.49, 0.46), (0.51, 0.54, 0.49, 0.46)])
    L[("jerarquico", 4)] = (1.0, [(0, 0, 1.0, 0.44), (0, 0.48, 0.32, 0.52),
                                  (0.34, 0.48, 0.32, 0.52), (0.68, 0.48, 0.32, 0.52)])
    # Asimetrico: grande a la izquierda, secundarias apiladas a la derecha.
    L[("asimetrico", 2)] = (1.0, [(0, 0, 0.62, 1.0), (0.66, 0, 0.34, 1.0)])
    L[("asimetrico", 3)] = (1.0, [(0, 0, 0.62, 1.0), (0.66, 0, 0.34, 0.49),
                                  (0.66, 0.53, 0.34, 0.47)])
    L[("asimetrico", 4)] = (1.0, [(0, 0, 0.62, 1.0), (0.66, 0, 0.34, 0.32),
                                  (0.66, 0.35, 0.34, 0.32), (0.66, 0.70, 0.34, 0.30)])
    # Bento: bloque 2x2 con piezas de distintos tamanos y un hueco de diseno.
    L[("bento", 2)] = (1.0, [(0, 0, 0.66, 1.0), (0.70, 0.10, 0.30, 0.80)])
    L[("bento", 3)] = (1.0, [(0, 0, 0.66, 0.66), (0.70, 0, 0.30, 0.30),
                             (0.70, 0.34, 0.30, 0.66)])
    L[("bento", 4)] = (1.0, [(0, 0, 0.66, 0.66), (0.70, 0, 0.30, 0.30),
                             (0.70, 0.34, 0.30, 0.66), (0, 0.70, 0.66, 0.30)])
    # Mosaico: filas que encajan sin espacios (celdas uniformes por fila).
    L[("mosaico", 2)] = (1.0, [(0, 0, 0.5, 1.0), (0.5, 0, 0.5, 1.0)])
    L[("mosaico", 3)] = (1.0, [(0, 0, 0.5, 0.5), (0.5, 0, 0.5, 0.5), (0, 0.54, 1.0, 0.46)])
    L[("mosaico", 4)] = (1.0, [(0, 0, 0.33, 0.5), (0.37, 0, 0.33, 0.5),
                               (0.74, 0, 0.26, 0.5), (0, 0.54, 1.0, 0.46)])
    # Puzzle: piezas desiguales encajadas sin huecos.
    L[("puzzle", 2)] = (1.0, [(0, 0, 0.55, 0.85), (0.59, 0.15, 0.41, 0.85)])
    L[("puzzle", 3)] = (1.0, [(0, 0, 0.6, 0.6), (0.64, 0, 0.36, 0.6), (0, 0.64, 1.0, 0.36)])
    L[("puzzle", 4)] = (1.0, [(0, 0, 0.6, 0.6), (0.64, 0, 0.36, 0.36),
                              (0.64, 0.40, 0.36, 0.60), (0, 0.64, 0.6, 0.36)])
    return L

LAYOUTS = _lay()
STYLES = ("grid", "bento", "mosaico", "puzzle", "jerarquico", "asimetrico")

def auto_style(N, ratio):
    """Seleccion automatica: N y proporcion de la primera imagen."""
    if N <= 2:
        return "grid"          # 2 imagenes: se mantiene el grid adaptativo
    if N == 3:
        if ratio >= 1.15: return "jerarquico"
        if ratio <= 0.85: return "asimetrico"
        return "mosaico"
    # N == 4
    if ratio >= 1.30: return "jerarquico"
    if ratio <= 0.85: return "asimetrico"
    return "bento"

def grid_dims(N, ratio):
    if N == 1:
        return 1, 1
    if ratio >= 1.2:
        return 1, N
    if ratio <= 0.8:
        return N, 1
    if N == 2:
        return 1, 2
    if N == 3:
        return 3, 1
    return 2, 2

# ── utilidades ──────────────────────────────────────────────────────────────
def segments(s):
    out = []; pos = 0
    for m in SEG.finditer(s):
        if m.start() > pos:
            out.append((s[pos:m.start()], "#FFFFFF"))
        out.append((m.group(1), "#" + m.group(2)))
        pos = m.end()
    if pos < len(s):
        out.append((s[pos:], "#FFFFFF"))
    return out or [(s, "#FFFFFF")]

def font_for(path, size):
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    try:
        return ImageFont.truetype("DejaVuSans-BoldOblique.ttf", size)
    except OSError:
        return ImageFont.load_default()

def text_width(draw, seg, font, stroke):
    return sum(draw.textbbox((0, 0), t, font=font, stroke_width=stroke)[2] for t, _ in seg)

def fit_font(draw, text, cfg):
    base = cfg.get("font_size", 60)
    mini = cfg.get("min_font_size", 28)
    limit = cfg.get("max_text_width", cfg["canvas"]["width"] - 2 * cfg.get("text_margin", 40))
    stroke = cfg.get("outline_width", 5)
    seg = segments(text)
    f = font_for(cfg.get("font", ""), base)
    w = text_width(draw, seg, f, stroke)
    size = base
    if w > limit and base > mini:
        size = max(mini, min(base, int(base * limit / w)))
        f = font_for(cfg.get("font", ""), size)
        w = text_width(draw, seg, f, stroke)
        while w > limit and size > mini:
            size -= 1
            f = font_for(cfg.get("font", ""), size)
            w = text_width(draw, seg, f, stroke)
    boxes = [draw.textbbox((0, 0), t, font=f, stroke_width=stroke) for t, _ in seg]
    h = max(b[3] for b in boxes) - min(b[1] for b in boxes)
    return f, seg, w, h, size

def draw_segments(im, seg, font, y, cfg, widths):
    draw = ImageDraw.Draw(im)
    stroke = cfg.get("outline_width", 5)
    x = (im.width - sum(widths)) / 2
    for (t, c), w in zip(seg, widths):
        draw.text((int(x), int(y)), t, font=font, fill=c,
                  stroke_width=stroke, stroke_fill="#000000")
        x += w

def rounded(img, radius):
    img = img.convert("RGBA")
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, img.width - 1, img.height - 1), radius=radius, fill=255)
    img.putalpha(mask)
    return img, mask

def place_shadow(bg, mask, x, y, blur, off_x, off_y, opacity):
    """Sombra exterior realmente difusa; se amplia la mascara antes del blur
    para evitar que el difuminado se recorte y quede cuadrado."""
    pad = max(8, int(math.ceil(blur * 3 + max(abs(off_x), abs(off_y)))))
    expanded = Image.new("L", (mask.width + 2 * pad, mask.height + 2 * pad), 0)
    expanded.paste(mask, (pad, pad))
    blurred = expanded.filter(ImageFilter.GaussianBlur(blur))
    alpha = blurred.point(lambda p: p * int(opacity) // 255)
    sh = Image.new("RGBA", expanded.size, (0, 0, 0, 0))
    sh.putalpha(alpha)
    bg.alpha_composite(sh, (int(x + off_x - pad), int(y + off_y - pad)))

def make_bg(src, cfg):
    W, H = cfg["canvas"]["width"], cfg["canvas"]["height"]
    iw, ih = src.size
    scale = max(W / iw, H / ih)
    bg = src.resize((round(iw * scale), round(ih * scale)), Image.Resampling.LANCZOS)
    left = (bg.width - W) // 2
    top = (bg.height - H) // 2
    bg = bg.crop((left, top, left + W, top + H))
    bg = bg.filter(ImageFilter.GaussianBlur(cfg.get("background_blur", 16)))
    bg = ImageEnhance.Brightness(bg).enhance(cfg.get("background_dim", 0.92)).convert("RGBA")
    return bg

def place_image(bg, img, x, y, w, h, cfg, fit="cover"):
    """Coloca una imagen en la celda (x,y,w,h). fit='cover' rellena la celda
    recortando centrado (nunca deforma); fit='contain' deja la imagen entera
    centrada (comportamiento de tarjeta simple/grid)."""
    x, y, w, h = int(round(x)), int(round(y)), int(round(w)), int(round(h))
    if fit == "cover":
        s = max(w / img.width, h / img.height)
        tw, th = max(1, round(img.width * s)), max(1, round(img.height * s))
        img2 = img.resize((tw, th), Image.Resampling.LANCZOS)
        # recorte centrado
        l = (tw - w) // 2; t = (th - h) // 2
        img2 = img2.crop((l, t, l + w, t + h))
    else:
        s = min(w / img.width, h / img.height)
        tw, th = max(1, round(img.width * s)), max(1, round(img.height * s))
        img2 = img.resize((tw, th), Image.Resampling.LANCZOS)
    radius = min(cfg.get("corner_radius", 18), img2.width // 2, img2.height // 2)
    img2, mask = rounded(img2, radius)
    px = x + (w - img2.width) // 2
    py = y + (h - img2.height) // 2
    sh = cfg.get("shadow", {})
    place_shadow(bg, mask, px, py,
                 sh.get("blur", 72), sh.get("offset", [10, 24])[0],
                 sh.get("offset", [10, 24])[1], sh.get("opacity", 42))
    bg.alpha_composite(img2, (px, py))
    return px, py, img2.width, img2.height

def load_cfg(preset_path):
    defaults = {
        "canvas": {"width": 1080, "height": 1920},
        "gap": 32,
        "font": "",
        "font_size": 60,
        "min_font_size": 28,
        "text_margin": 60,
        "outline_width": 5,
        "foreground_max_width": 1000,
        "foreground_max_height": 1120,
        "corner_radius": 18,
        "grid_gap": 24,
        "grid_padding": 60,
        "outer_margin": 90,
        "shadow": {
            "near_blur": 32, "near_offset": [4, 12], "near_opacity": 60,
            "far_blur": 72, "far_offset": [16, 36], "far_opacity": 48,
        },
        "background_blur": 16,
        "background_dim": 0.92,
    }
    if preset_path:
        with open(preset_path) as fh:
            defaults.update(json.load(fh))
    return defaults

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", help="1-4 imagenes de entrada; el ULTIMO es la salida")
    ap.add_argument("--top", required=True)
    ap.add_argument("--bottom", required=True)
    ap.add_argument("--preset", default=None)
    ap.add_argument("--style", default="auto",
                    choices=("auto",) + STYLES,
                    help="estilo de collage (defecto: auto segun N y proporcion)")
    a = ap.parse_args()
    if len(a.inputs) < 2:
        ap.error("hace falta al menos una imagen de entrada y una salida")
    *src_paths, out_path = a.inputs
    if len(src_paths) > 4:
        print("AVISO: mas de 4 imagenes; se usan las 4 primeras", flush=True)
        src_paths = src_paths[:4]

    cfg = load_cfg(a.preset)
    W, H = cfg["canvas"]["width"], cfg["canvas"]["height"]
    srcs = [Image.open(p).convert("RGB") for p in src_paths]
    N = len(srcs)
    ratio = srcs[0].width / srcs[0].height

    style = auto_style(N, ratio) if a.style == "auto" else a.style
    if style != "grid" and (style, N) not in LAYOUTS:
        style = "grid"   # estilos nuevos solo 2-4 con layout definido; N=1 simple

    bg = make_bg(srcs[0], cfg)
    probe = ImageDraw.Draw(bg)
    top_font, top_seg, top_w, top_h, top_size = fit_font(probe, a.top, cfg)
    bot_font, bot_seg, bot_w, bot_h, bot_size = fit_font(probe, a.bottom, cfg)

    gap = cfg.get("gap", 32)
    g = cfg.get("grid_gap", 24)
    pd = cfg.get("grid_padding", 30)
    outm = cfg.get("outer_margin", 90)
    usable = H - 2 * outm - top_h - bot_h - 2 * gap
    content_w = min(W - 2 * pd, cfg.get("foreground_max_width", 1000))
    cells = []   # (x, y, w, h) en px absolutos del canvas

    if style == "grid" or N == 1:
        cols, rows = grid_dims(N, ratio, ) if N > 1 else (1, 1)
        cell_w = (content_w - (cols - 1) * g) / cols
        cell_h = cell_w / ratio if ratio else cell_w
        collage_h = rows * cell_h + (rows - 1) * g
        if collage_h > usable:
            usable2 = max(usable, (rows - 1) * g + 60)
            scale = max(0.2, min(1.0, (usable2 - (rows - 1) * g) / (rows * cell_h)))
            cell_w *= scale
            cell_h = cell_w / ratio if ratio else cell_w
            collage_h = rows * cell_h + (rows - 1) * g
        cw = cols * cell_w + (cols - 1) * g
        cx0 = (W - cw) // 2
        for i in range(N):
            r_i, c_i = divmod(i, cols)
            cells.append((cx0 + c_i * (cell_w + g),
                          0 + r_i * (cell_h + g),
                          cell_w, cell_h))
        fit = "contain" if N == 1 else ("contain" if style == "grid" else "cover")
    else:
        A_h, rects = LAYOUTS[(style, N)]
        collage_w = content_w
        collage_h = A_h * collage_w
        scale = 1.0
        if collage_h > usable:
            scale = max(0.2, min(1.0, usable / collage_h))
        cw = collage_w * scale
        ch = collage_h * scale
        cx0 = (W - cw) / 2
        for (rx, ry, rw, rh) in rects:
            cells.append((cx0 + rx * cw, ry * ch, rw * cw, rh * ch))
        collage_h = ch
        fit = "cover"

    group = top_h + gap + collage_h + gap + bot_h
    y0 = (H - group) // 2
    ty = y0
    cy = ty + top_h + gap
    by = cy + collage_h + gap

    for (x, y, w, h), src in zip(cells, srcs):
        place_image(bg, src, x + cy * 0 + 0, y + cy, w, h, cfg, fit=fit)

    tw = [probe.textbbox((0, 0), t, font=top_font, stroke_width=cfg.get("outline_width", 5))[2] for t, _ in top_seg]
    bw = [probe.textbbox((0, 0), t, font=bot_font, stroke_width=cfg.get("outline_width", 5))[2] for t, _ in bot_seg]
    draw_segments(bg, top_seg, top_font, ty, cfg, tw)
    draw_segments(bg, bot_seg, bot_font, by, cfg, bw)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    bg.convert("RGB").save(out, quality=95, optimize=True)
    print(json.dumps({
        "output": str(out), "width": W, "height": H,
        "images": N, "ratio": round(ratio, 3), "style": style,
        "top": {"y": ty, "font_size": top_size}, "bottom": {"y": by, "font_size": bot_size},
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
