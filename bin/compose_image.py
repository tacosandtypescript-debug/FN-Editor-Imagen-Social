#!/usr/bin/env python3
"""Compone una imagen vertical configurable con fondo cover blur, uno o
varios sujetos (collage de 2+ imagenes), esquinas redondeadas, sombra exterior de
dos capas y texto superior/inferior segmentado con palabras de color.

Uso:
  compose_image.py imagen1.jpg [imagen2.jpg ...] salida.png \
      --top 'TEXTO {CLAVE|FFFFFF}' --bottom '...' [--preset preset.json]

El ULTIMO argumento posicional es la salida; todos los anteriores son imagenes
de entrada (1 = tarjeta simple, 2+ = collage).

Estilos de collage (--style):
  auto         -> usa grid para una imagen y adaptive para collages (defecto)
  grid         -> cuadricula uniforme para cualquier numero de imagenes
  adaptive     -> clasifica cada imagen (horizontal/cuadrada/vertical) y le
                  asigna una celda 16:9, 1:1 o 9:16 conservando el orden
  bento        -> bloques cuadrados y rectangulares de distintos tamanos, con
                  huecos de diseno (muy ordenado/moderno)
  mosaico      -> celdas que encajan sin espacios, filas de distinta altura
  puzzle       -> piezas rectangulares desiguales que encajan sin huecos
  jerarquico   -> una imagen grande protagonista + secundarias mas pequenas
  asimetrico   -> una imagen grande y varias pequenas alrededor

El calculo de texto/posiciones es el mismo para todos los estilos; cada celda
rellena su rectangulo con cover centrado (nunca deforma; recorta los bordes
justos). Usa --fit contain para conservar la imagen completa en cada celda.
"""
import argparse, json, math, re, sys
from collections.abc import Mapping
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance, ImageOps, ImageStat
from runtime_config import (
    DEFAULT_MAX_IMAGES,
    MAX_CANVAS_EDGE,
    MAX_CANVAS_PIXELS,
    MAX_SOURCE_PIXELS,
)
from render_backend import (
    BackendUnavailable,
    make_background as make_gpu_background,
    resolve_backend,
    resize_image,
    should_use_gpu_background,
)

SEG = re.compile(r"\{([^{}|]+)\|([0-9A-Fa-f]{6})\}")
HIGHLIGHT_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
SPANISH_FUNCTION_WORDS = {
    "a", "al", "ante", "bajo", "con", "contra", "de", "del", "desde",
    "e", "el", "en", "entre", "hacia", "hasta", "la", "las", "lo",
    "los", "o", "para", "por", "que", "se", "sin", "sobre", "un",
    "una", "unos", "unas", "y", "u",
}

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
    # Mosaico: filas compactas con separación visual (celdas uniformes por fila).
    L[("mosaico", 2)] = (1.0, [(0, 0, 0.5, 1.0), (0.5, 0, 0.5, 1.0)])
    L[("mosaico", 3)] = (1.0, [(0, 0, 0.5, 0.5), (0.5, 0, 0.5, 0.5), (0, 0.54, 1.0, 0.46)])
    L[("mosaico", 4)] = (1.0, [(0, 0, 0.33, 0.5), (0.37, 0, 0.33, 0.5),
                               (0.74, 0, 0.26, 0.5), (0, 0.54, 1.0, 0.46)])
    # Puzzle: piezas rectangulares desiguales con separación visual.
    L[("puzzle", 2)] = (1.0, [(0, 0, 0.55, 0.85), (0.59, 0.15, 0.41, 0.85)])
    L[("puzzle", 3)] = (1.0, [(0, 0, 0.6, 0.6), (0.64, 0, 0.36, 0.6), (0, 0.64, 1.0, 0.36)])
    L[("puzzle", 4)] = (1.0, [(0, 0, 0.6, 0.6), (0.64, 0, 0.36, 0.36),
                              (0.64, 0.40, 0.36, 0.60), (0, 0.64, 0.6, 0.36)])
    return L

LAYOUTS = _lay()
STYLES = ("grid", "adaptive", "bento", "mosaico", "puzzle", "jerarquico", "asimetrico")


def aspect_bucket(size):
    """Classify a source image and choose its canonical cell aspect ratio."""
    ratio = size[0] / size[1]
    if ratio >= 1.25:
        return "landscape", 16 / 9
    if ratio <= 0.8:
        return "portrait", 9 / 16
    return "square", 1.0


def adaptive_layout(
    images, canvas_width, content_width, max_height, gap,
    stack_square_pair=False, equal_pair_cells=False,
):
    """Create justified rows whose cells follow each image's orientation.

    The order of the source images is preserved. Each row is laid out like a
    contact sheet: horizontal images get 16:9 cells, square images 1:1 cells,
    and vertical images 9:16 cells. Rows are scaled together if needed.
    ``equal_pair_cells`` is used by the square preset when a two-image card
    must use two equal 1:1 panels; ``contain`` then preserves mixed sources.
    """
    max_height = max(float(max_height), 1.0)
    gap = max(float(gap), 0.0)
    items = []
    for index, image in enumerate(images):
        bucket, aspect = aspect_bucket(image.size)
        items.append({"index": index, "bucket": bucket, "aspect": aspect})

    if equal_pair_cells and len(images) == 2:
        cell = max(1.0, min(max_height, (content_width - gap) / 2))
        total_width = 2 * cell + gap
        start_x = (canvas_width - total_width) / 2
        return [
            (start_x, 0.0, cell, cell),
            (start_x + cell + gap, 0.0, cell, cell),
        ], cell

    average_aspect = sum(item["aspect"] for item in items) / len(items)
    target_height = math.sqrt(
        max(content_width, 1) * max(max_height, 1) /
        max(len(items) * average_aspect, 1)
    )
    target_height = max(180.0, min(480.0, target_height))
    break_below = target_height * 0.78

    if stack_square_pair:
        rows = [[item] for item in items]
    else:
        rows = []
        current = []
        for item in items:
            candidate = current + [item]
            natural_height = (
                content_width - gap * (len(candidate) - 1)
            ) / sum(part["aspect"] for part in candidate)
            if current and natural_height < break_below:
                rows.append(current)
                current = [item]
            else:
                current = candidate
        if current:
            rows.append(current)

    row_heights = []
    for row in rows:
        natural_height = (
            content_width - gap * (len(row) - 1)
        ) / sum(item["aspect"] for item in row)
        row_heights.append(min(natural_height, max_height))

    # Reserve the inter-row gaps before scaling the rows. Otherwise the cells
    # fit individually but the last row can still overflow the safe area.
    row_gap = min(
        gap,
        max(0.0, (max_height - len(rows)) / max(1, len(rows) - 1)),
    )
    gap_total = row_gap * max(0, len(rows) - 1)
    row_sum = sum(row_heights)
    if row_sum + gap_total > max_height:
        available_height = max(1.0, max_height - gap_total)
        scale = available_height / max(row_sum, 1.0)
        row_heights = [height * scale for height in row_heights]
    total_height = sum(row_heights) + gap_total

    content_x = (canvas_width - content_width) / 2
    cells = [None] * len(images)
    y = 0.0
    for row, row_height in zip(rows, row_heights):
        row_width = sum(item["aspect"] * row_height for item in row)
        row_width += gap * max(0, len(row) - 1)
        x = content_x + (content_width - row_width) / 2
        for item in row:
            width = item["aspect"] * row_height
            cells[item["index"]] = (x, y, width, row_height)
            x += width + gap
        y += row_height + row_gap
    return cells, total_height

def auto_style(N):
    """Seleccion automatica basada en el numero de fuentes."""
    if N > 1:
        return "adaptive"
    return "grid"

def grid_dims(N, ratio):
    """Choose a balanced grid for N images and a target area ratio."""
    if N == 1:
        return 1, 1
    ratio = max(float(ratio), 0.01)
    best = None
    for cols in range(1, N + 1):
        rows = math.ceil(N / cols)
        grid_ratio = cols / rows
        empty = cols * rows - N
        score = abs(math.log(grid_ratio / ratio)) + empty * 0.55
        candidate = (score, empty, abs(cols - rows), cols, rows)
        if best is None or candidate < best[0]:
            best = (candidate, cols, rows)
    return best[1], best[2]


def _validate_canvas_dimensions(width, height, label="el lienzo"):
    if isinstance(width, bool) or not isinstance(width, int):
        raise ValueError(f"{label}: el ancho debe ser un entero")
    if isinstance(height, bool) or not isinstance(height, int):
        raise ValueError(f"{label}: el alto debe ser un entero")
    if width <= 0 or height <= 0:
        raise ValueError(f"{label}: el ancho y el alto deben ser positivos")
    if width > MAX_CANVAS_EDGE or height > MAX_CANVAS_EDGE:
        raise ValueError(
            f"{label}: cada lado debe ser como maximo {MAX_CANVAS_EDGE}px"
        )
    if width * height > MAX_CANVAS_PIXELS:
        raise ValueError(
            f"{label}: el area no puede superar {MAX_CANVAS_PIXELS:,} pixeles"
        )


def _validate_number(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} debe ser numerico")
    if not math.isfinite(float(value)):
        raise ValueError(f"{label} debe ser finito")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} debe ser >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} debe ser <= {maximum}")


def _validate_offset(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} debe tener exactamente dos valores")
    for index, item in enumerate(value):
        _validate_number(item, f"{label}[{index}]")


def validate_config(cfg):
    """Validate the supported preset schema before rendering starts."""
    if not isinstance(cfg, Mapping):
        raise ValueError("el preset debe contener un objeto JSON")

    canvas = cfg.get("canvas")
    if not isinstance(canvas, Mapping):
        raise ValueError("canvas debe ser un objeto con width y height")
    if "width" not in canvas or "height" not in canvas:
        raise ValueError("canvas debe incluir width y height")
    _validate_canvas_dimensions(canvas["width"], canvas["height"], "canvas")

    numeric_rules = {
        "gap": (0, None),
        "font_size": (1, None),
        "min_font_size": (1, None),
        "max_text_width": (1, None),
        "text_margin": (0, None),
        "outline_width": (0, None),
        "foreground_max_width": (1, None),
        "foreground_max_height": (0, None),
        "corner_radius": (0, None),
        "grid_gap": (0, None),
        "grid_padding": (0, None),
        "outer_margin": (0, None),
        "line_gap": (0, None),
        "watermark_gap": (0, None),
        "background_blur": (0, None),
        "background_dim": (0, 2),
    }
    for key, (minimum, maximum) in numeric_rules.items():
        if key in cfg:
            _validate_number(cfg[key], key, minimum, maximum)

    if "font_size" in cfg and "min_font_size" in cfg:
        if cfg["min_font_size"] > cfg["font_size"]:
            raise ValueError("min_font_size no puede superar font_size")

    width, height = canvas["width"], canvas["height"]
    if 2 * cfg.get("grid_padding", 0) >= width:
        raise ValueError("grid_padding deja menos de un pixel de ancho util")
    if 2 * cfg.get("text_margin", 0) >= width:
        raise ValueError("text_margin deja menos de un pixel de ancho util")
    if 2 * cfg.get("outer_margin", 0) >= height:
        raise ValueError("outer_margin deja menos de un pixel de alto util")

    font_value = cfg.get("font", "")
    if not isinstance(font_value, str):
        raise ValueError("font debe ser una ruta de texto")

    for section_name in ("shadow", "text_shadow"):
        section = cfg.get(section_name)
        if section is None:
            continue
        if not isinstance(section, Mapping):
            raise ValueError(f"{section_name} debe ser un objeto")
        if "blur" in section:
            _validate_number(section["blur"], f"{section_name}.blur", 0)
        if "opacity" in section:
            _validate_number(section["opacity"], f"{section_name}.opacity", 0, 255)
        if "offset" in section:
            _validate_offset(section["offset"], f"{section_name}.offset")

    watermark = cfg.get("watermark")
    if watermark is not None:
        if not isinstance(watermark, Mapping):
            raise ValueError("watermark debe ser un objeto")
        if "text" in watermark and not isinstance(watermark["text"], str):
            raise ValueError("watermark.text debe ser texto")
        if "size" in watermark:
            _validate_number(watermark["size"], "watermark.size", 1)
        if "opacity" in watermark:
            _validate_number(watermark["opacity"], "watermark.opacity", 0, 255)
        if "bottom_margin" in watermark:
            _validate_number(watermark["bottom_margin"], "watermark.bottom_margin", 0)
        if "side_mode" in watermark and watermark["side_mode"] not in {
            "auto", "side", "lateral"
        }:
            raise ValueError("watermark.side_mode debe ser auto, side o lateral")
        if "side" in watermark and watermark["side"] not in {"auto", "left", "right"}:
            raise ValueError("watermark.side debe ser auto, left o right")

    if "equal_pair_cells" in cfg and not isinstance(cfg["equal_pair_cells"], bool):
        raise ValueError("equal_pair_cells debe ser booleano")

    palette = cfg.get("palette")
    if palette is not None and not isinstance(palette, Mapping):
        raise ValueError("palette debe ser un objeto")
    return cfg


def _merge_config(base, override):
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), Mapping):
            _merge_config(base[key], value)
        else:
            base[key] = value
    return base


def _resolve_preset_path(preset_path):
    requested = Path(preset_path)
    if requested.is_absolute():
        return requested
    candidates = [
        Path.cwd() / requested,
        Path(__file__).resolve().parents[1] / requested,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def format_dimensions(value, default=(1080, 1920), short_edge=1080):
    """Resolve a ratio such as 1:1, 4:5, 16:9 or 9:16 to pixels."""
    if not value:
        dimensions = (int(default[0]), int(default[1]))
        _validate_canvas_dimensions(*dimensions, "el lienzo")
        return dimensions
    match = re.fullmatch(r"\s*(\d+)\s*[:x/]\s*(\d+)\s*", str(value))
    if not match:
        raise ValueError("el formato debe ser PROPORCION, por ejemplo 1:1, 4:5 o 9:16")
    aspect_w, aspect_h = int(match.group(1)), int(match.group(2))
    if aspect_w <= 0 or aspect_h <= 0:
        raise ValueError("la proporcion debe tener valores positivos")
    short_edge = int(short_edge)
    if short_edge <= 0:
        raise ValueError("el lado corto debe ser positivo")
    if aspect_w >= aspect_h:
        dimensions = (round(short_edge * aspect_w / aspect_h), short_edge)
    else:
        dimensions = (short_edge, round(short_edge * aspect_h / aspect_w))
    _validate_canvas_dimensions(*dimensions, "la salida")
    return dimensions


RESOLUTION_SCALES = {"native": 1, "4k": 2}
PIXEL_CONFIG_KEYS = {
    "gap", "font_size", "min_font_size", "max_text_width", "text_margin",
    "outline_width", "foreground_max_width", "foreground_max_height",
    "corner_radius", "grid_gap", "grid_padding", "outer_margin", "line_gap",
    "watermark_gap", "layout_shift_y", "background_blur",
}


def scale_config_for_resolution(cfg, resolution):
    """Scale canvas geometry and typography without changing colors/opacities."""
    try:
        scale = RESOLUTION_SCALES[resolution]
    except KeyError as exc:
        raise ValueError("la resolución debe ser native o 4k") from exc
    if scale == 1:
        return scale

    canvas = cfg["canvas"]
    canvas["width"] = round(canvas["width"] * scale)
    canvas["height"] = round(canvas["height"] * scale)
    for key in PIXEL_CONFIG_KEYS:
        value = cfg.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            cfg[key] = round(value * scale)
    for section_name in ("watermark", "text_shadow", "shadow"):
        section = cfg.get(section_name)
        if not isinstance(section, Mapping):
            continue
        for key in ("size", "bottom_margin", "side_gap", "blur"):
            value = section.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                section[key] = round(value * scale)
        offset = section.get("offset")
        if isinstance(offset, (list, tuple)) and len(offset) == 2:
            section["offset"] = [round(item * scale) for item in offset]
    return scale


def positive_int(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("debe ser un entero positivo") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("debe ser un entero positivo")
    return parsed

# ── utilidades ──────────────────────────────────────────────────────────────
def segments(s, default_color="#FFFFFF"):
    out = []; pos = 0
    for m in SEG.finditer(s):
        if m.start() > pos:
            out.append((s[pos:m.start()], default_color))
        out.append((m.group(1), "#" + m.group(2)))
        pos = m.end()
    if pos < len(s):
        out.append((s[pos:], default_color))
    return out or [(s, default_color)]


def validate_text_markup(*texts):
    """Reject empty text and highlights that violate the editorial contract."""
    accent_colors = set()
    for label, text in zip(("superior", "inferior"), texts):
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"el texto {label} no puede estar vacío")
        for match in SEG.finditer(text):
            marked_words = [word.casefold() for word in HIGHLIGHT_WORD_RE.findall(match.group(1))]
            if any(word in SPANISH_FUNCTION_WORDS for word in marked_words):
                raise ValueError(
                    f"no se puede colorear una palabra funcional en el texto {label}: "
                    f"'{match.group(1)}'"
                )
            accent_colors.add("#" + match.group(2).upper())
    if len(accent_colors) > 2:
        raise ValueError("usa como máximo dos colores de acento entre el titular y el contexto")

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


def load_image(path, label="imagen"):
    """Open an image into memory while closing the underlying file handle."""
    path = Path(path)
    try:
        with Image.open(path) as opened:
            width, height = opened.size
            if width <= 0 or height <= 0:
                raise ValueError("sus dimensiones deben ser positivas")
            if width * height > MAX_SOURCE_PIXELS:
                raise ValueError(
                    f"supera el maximo de {MAX_SOURCE_PIXELS:,} pixeles"
                )
            return ImageOps.exif_transpose(opened).convert("RGB")
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} '{path}' no es valida: {exc}") from exc

def block_at_size(draw, text, cfg, size):
    """Measure a multiline text block using one font size for every line."""
    stroke = int(cfg.get("outline_width", 5))
    default_color = cfg.get("text_color", "#FFFFFF")
    font = font_for(cfg.get("font", ""), int(size))
    parts = []
    for line in text.split("\n"):
        seg = segments(line, default_color)
        boxes = [draw.textbbox((0, 0), t, font=font, stroke_width=stroke)
                 for t, _ in seg]
        widths = [box[2] for box in boxes]
        parts.append({
            "f": font,
            "seg": seg,
            "widths": widths,
            "w": sum(widths),
            "h": max(box[3] for box in boxes) - min(box[1] for box in boxes),
        })
    line_gap = int(cfg.get("line_gap", 10))
    height = sum(part["h"] for part in parts) + line_gap * max(0, len(parts) - 1)
    return {"parts": parts, "height": height, "size": int(size)}


def fit_block(draw, text, cfg):
    """Fit every line horizontally while preserving one size per block."""
    base = int(cfg.get("font_size", 60))
    mini = int(cfg.get("min_font_size", 28))
    limit = int(cfg.get("max_text_width", cfg["canvas"]["width"] - 2 * cfg.get("text_margin", 40)))
    for size in range(base, mini - 1, -1):
        block = block_at_size(draw, text, cfg, size)
        if all(part["w"] <= limit for part in block["parts"]):
            return block
    raise ValueError(
        f"el texto no cabe en {limit}px incluso con la fuente mínima ({mini}px); "
        "divide el texto en más líneas"
    )

def draw_segments(im, seg, font, y, cfg, widths):
    draw = ImageDraw.Draw(im)
    stroke = cfg.get("outline_width", 5)
    x0 = (im.width - sum(widths)) / 2

    # Sombra independiente y difusa, separada del contorno negro del texto.
    shadow_cfg = cfg.get("text_shadow", {})
    shadow_opacity = max(0, min(255, int(shadow_cfg.get("opacity", 0))))
    if shadow_opacity:
        shadow_layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_layer)
        offset = shadow_cfg.get("offset", [0, 0])
        off_x = int(offset[0]) if len(offset) > 0 else 0
        off_y = int(offset[1]) if len(offset) > 1 else 0
        x = x0
        for (t, _), w in zip(seg, widths):
            shadow_draw.text(
                (int(x + off_x), int(y + off_y)), t, font=font,
                fill=(0, 0, 0, shadow_opacity), stroke_width=stroke,
                stroke_fill=(0, 0, 0, shadow_opacity),
            )
            x += w
        blur = max(0, float(shadow_cfg.get("blur", 0)))
        if blur:
            shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(blur))
        im.alpha_composite(shadow_layer)

    x = x0
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

def _side_for_watermark(im):
    """Elige el lateral con menos contenido (menor contraste en la franja)."""
    g = im.convert("L")
    strip = 60
    left = g.crop((0, 0, strip, im.height))
    right = g.crop((im.width - strip, 0, im.width, im.height))
    s_l = ImageStat.Stat(left).stddev[0]
    s_r = ImageStat.Stat(right).stddev[0]
    return "left" if s_l <= s_r else "right"

def _draw_watermark_side(im, cfg, layer, text, font, opacity, side):
    """Marca lateral: texto completo rotado 90°, centrado en el punto medio
    entre la zona segura y el borde del lienzo, dentro del lienzo y sin
    cortarse."""
    tlayer = Image.new("RGBA", (im.width, im.height), (0, 0, 0, 0))
    td = ImageDraw.Draw(tlayer)
    box = td.textbbox((0, 0), text, font=font)
    td.text((0 - box[0], 0 - box[1]), text, font=font, fill=(255, 255, 255, opacity),
            stroke_width=1, stroke_fill=(0, 0, 0, max(0, opacity // 2)))
    rot = tlayer.rotate(90, expand=True, resample=Image.Resampling.BICUBIC)
    bbox = rot.getbbox()
    rot = rot.crop(bbox)
    safe_x = cfg.get("text_margin", 60)
    horizontal = rot.width
    inset = 40  # px dentro del límite de la zona segura (avance hacia la imagen)
    if side == "left":
        px = safe_x - inset - horizontal
    else:
        px = (im.width - safe_x) - inset - horizontal
    px = max(2, min(px, im.width - horizontal - 2))
    py = (im.height - rot.height) // 2
    layer.alpha_composite(rot, (px, py))

def draw_watermark(im, cfg, n_images=1):
    """Marca de agua del creador: CÓDIGO: KHETZALGG.

    - 1 imagen / tarjeta simple: centrada abajo (comportamiento historico).
    - Collage (2+ imagenes) o 'side_mode=auto': colocada en el lateral con
      menos contenido, rotada 90° como una sola pieza, dentro del lienzo,
      fuera de la zona segura y sin cortarse.
    """
    wm = cfg.get("watermark", {})
    text = wm.get("text", "")
    if not text:
        return
    font = font_for(wm.get("font", cfg.get("font", "")), int(wm.get("size", 46)))
    opacity = int(wm.get("opacity", 125))
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    side_mode = wm.get("side_mode", "auto")
    if side_mode in ("side", "lateral") or (side_mode == "auto" and n_images >= 2):
        side = wm.get("side", "auto")
        if side not in ("left", "right"):
            side = _side_for_watermark(im)
        _draw_watermark_side(im, cfg, layer, text, font, opacity, side)
    else:
        margin = int(wm.get("bottom_margin", cfg.get("outer_margin", 90) + 55))
        d = ImageDraw.Draw(layer)
        box = d.textbbox((0, 0), text, font=font)
        x = (im.width - (box[2] - box[0])) // 2 - box[0]
        y = im.height - margin - box[3]
        d.text((x, y), text, font=font, fill=(255, 255, 255, opacity),
               stroke_width=1, stroke_fill=(0, 0, 0, max(0, opacity // 2)))
    im.alpha_composite(layer)


def watermark_geometry(height, cfg, n_images=1):
    """Return the visible watermark bounds so the layout can avoid it."""
    wm = cfg.get("watermark", {})
    text = wm.get("text", "")
    if not text:
        return None
    side_mode = wm.get("side_mode", "auto")
    if side_mode in ("side", "lateral") or (side_mode == "auto" and n_images >= 2):
        return None
    font = font_for(wm.get("font", cfg.get("font", "")), int(wm.get("size", 46)))
    layer = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    box = ImageDraw.Draw(layer).textbbox((0, 0), text, font=font)
    margin = int(wm.get("bottom_margin", cfg.get("outer_margin", 90) + 55))
    y = height - margin - box[3]
    return {"top": y + box[1], "bottom": y + box[3], "font": font, "box": box}

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

def make_bg(src, cfg, backend="cpu"):
    if backend == "gpu" and should_use_gpu_background(cfg):
        return make_gpu_background(src, cfg, backend)
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

def equal_square_source(image, cfg, backend="cpu"):
    """Create a square panel source without cropping its foreground image.

    The enlarged/cropped copy is only a blurred panel background. The original
    image is pasted at its native aspect ratio on top, so every source corner
    remains available when the panel is later rendered with ``contain``.
    """
    side = max(image.size)
    iw, ih = image.size
    scale = max(side / iw, side / ih)
    bg = resize_image(
        image,
        (max(1, round(iw * scale)), max(1, round(ih * scale))),
        backend,
    )
    left = (bg.width - side) // 2
    top = (bg.height - side) // 2
    bg = bg.crop((left, top, left + side, top + side))
    blur = max(0.0, float(cfg.get("background_blur", 16)))
    if blur:
        bg = bg.filter(ImageFilter.GaussianBlur(blur))
    bg = ImageEnhance.Brightness(bg).enhance(float(cfg.get("background_dim", 0.92)))
    panel = bg.convert("RGB")
    panel.paste(image, ((side - iw) // 2, (side - ih) // 2))
    return panel


def place_image(bg, img, x, y, w, h, cfg, fit="cover", backend="cpu"):
    """Coloca una imagen en la celda (x,y,w,h). fit='cover' rellena la celda
    recortando centrado (nunca deforma); fit='contain' deja la imagen entera
    centrada (comportamiento de tarjeta simple/grid)."""
    x, y, w, h = int(round(x)), int(round(y)), int(round(w)), int(round(h))
    if fit == "cover":
        s = max(w / img.width, h / img.height)
        tw, th = max(1, round(img.width * s)), max(1, round(img.height * s))
        img2 = resize_image(img, (tw, th), backend)
        # recorte centrado
        l = (tw - w) // 2; t = (th - h) // 2
        img2 = img2.crop((l, t, l + w, t + h))
    else:
        s = min(w / img.width, h / img.height)
        tw, th = max(1, round(img.width * s)), max(1, round(img.height * s))
        img2 = resize_image(img, (tw, th), backend)
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
        "font_size": 72,
        "min_font_size": 32,
        "text_margin": 60,
        "outline_width": 6,
        "foreground_max_width": 1000,
        "foreground_max_height": 1120,
        "corner_radius": 54,
        "grid_gap": 24,
        "grid_padding": 60,
        "outer_margin": 90,
        "shadow": {"blur": 28, "offset": [-8, 12], "opacity": 48},
        "background_blur": 16,
        "background_dim": 0.92,
    }
    preset_file = None
    if preset_path:
        preset_file = _resolve_preset_path(preset_path)
        try:
            with open(preset_file, encoding="utf-8") as fh:
                loaded = json.load(fh)
        except FileNotFoundError as exc:
            raise ValueError(f"no se encontro el preset: {preset_path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"el preset no contiene JSON valido ({preset_path}): {exc.msg}"
            ) from exc
        except OSError as exc:
            raise ValueError(f"no se pudo leer el preset {preset_path}: {exc}") from exc
        if not isinstance(loaded, Mapping):
            raise ValueError("el preset debe contener un objeto JSON")
        _merge_config(defaults, loaded)
    validate_config(defaults)

    # Resolve bundled fonts from the repository instead of depending on the
    # absolute path of the machine that created the preset.
    font_value = defaults.get("font", "")
    if font_value:
        font_path = Path(str(font_value))
        candidates = [font_path] if font_path.is_absolute() else [Path.cwd() / font_path]
        if not font_path.is_absolute():
            if preset_file:
                candidates.append(preset_file.resolve().parent / font_path)
            candidates.append(Path(__file__).resolve().parents[1] / font_path)
        for candidate in candidates:
            if candidate.is_file():
                defaults["font"] = str(candidate)
                break
        else:
            raise ValueError(f"no se encontro la fuente del preset: {font_value}")
    validate_config(defaults)
    return defaults

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", help="imagenes de entrada (1 o mas); el ULTIMO es la salida")
    ap.add_argument("--top", required=True)
    ap.add_argument("--bottom", required=True)
    ap.add_argument("--preset", default=None)
    ap.add_argument(
        "--max-images", type=positive_int, default=DEFAULT_MAX_IMAGES,
        help=f"maximo de imagenes de entrada (defecto: {DEFAULT_MAX_IMAGES})",
    )
    ap.add_argument("--background", default=None,
                    help="imagen externa para el fondo desenfocado")
    ap.add_argument("--style", default="auto",
                    choices=("auto",) + STYLES,
                    help="estilo de collage (defecto: auto segun la cantidad de imagenes)")
    ap.add_argument(
        "--format", dest="output_format", default=None,
        help="proporcion de salida, por ejemplo 1:1, 4:5, 16:9 o 9:16",
    )
    ap.add_argument(
        "--fit", choices=("auto", "cover", "contain"), default="auto",
        help="recorte por celda: auto usa contain para una imagen y cover para collages",
    )
    ap.add_argument(
        "--backend", choices=("auto", "cpu", "gpu"), default="auto",
        help="backend de renderizado: auto usa CUDA si está disponible (defecto: auto)",
    )
    ap.add_argument(
        "--resolution", choices=tuple(RESOLUTION_SCALES), default="native",
        help="resolución de exportación: native o 4k (2160 px de lado corto)",
    )
    a = ap.parse_args()
    try:
        validate_text_markup(a.top, a.bottom)
    except ValueError as exc:
        ap.error(str(exc))
    try:
        backend = resolve_backend(a.backend)
    except (BackendUnavailable, ValueError) as exc:
        ap.error(str(exc))
    if len(a.inputs) < 2:
        ap.error("hace falta al menos una imagen de entrada y una salida")
    *src_paths, out_path = a.inputs
    if len(src_paths) > a.max_images:
        ap.error(
            f"se recibieron {len(src_paths)} imagenes; el maximo configurado es {a.max_images}"
        )
    N = len(src_paths)
    style = auto_style(N) if a.style == "auto" else a.style
    if style not in {"grid", "adaptive"} and (style, N) not in LAYOUTS:
        supported = sorted(count for layout, count in LAYOUTS if layout == style)
        supported_text = ", ".join(str(count) for count in supported)
        ap.error(
            f"el estilo '{style}' no admite {N} imagenes; admite {supported_text}"
        )
    try:
        cfg = load_cfg(a.preset)
    except ValueError as exc:
        ap.error(str(exc))
    try:
        resolution_scale = scale_config_for_resolution(cfg, a.resolution)
    except ValueError as exc:
        ap.error(str(exc))
    try:
        W, H = format_dimensions(
            a.output_format,
            (cfg["canvas"]["width"], cfg["canvas"]["height"]),
            short_edge=min(cfg["canvas"]["width"], cfg["canvas"]["height"]),
        )
    except ValueError as exc:
        ap.error(str(exc))
    cfg["canvas"] = {"width": W, "height": H}
    try:
        validate_config(cfg)
        srcs = [load_image(p) for p in src_paths]
    except ValueError as exc:
        ap.error(str(exc))
    equal_pair_cells_active = (
        style == "adaptive"
        and bool(cfg.get("equal_pair_cells", False))
        and N == 2
        and W == H
    )
    layout_srcs = (
        [equal_square_source(src, cfg, backend) for src in srcs]
        if equal_pair_cells_active
        else srcs
    )
    ratio = srcs[0].width / srcs[0].height
    if N > 1 and len({src.size for src in srcs}) > 1:
        warning = (
            "AVISO: las imagenes no tienen el mismo tamano; "
            "se normalizaran en paneles cuadrados iguales"
            if equal_pair_cells_active
            else "AVISO: las imagenes no tienen el mismo tamano; "
            "se ajustaran a su orientacion"
        )
        print(warning, file=sys.stderr, flush=True)

    try:
        bg_src = load_image(a.background, "el fondo") if a.background else srcs[0]
    except ValueError as exc:
        ap.error(str(exc))
    bg = make_bg(bg_src, cfg, backend)
    probe = ImageDraw.Draw(bg)
    gap = cfg.get("gap", 32)
    g = cfg.get("grid_gap", 24)
    pd = cfg.get("grid_padding", 30)
    outm = cfg.get("outer_margin", 90)
    line_gap = int(cfg.get("line_gap", 10))
    mini = int(cfg.get("min_font_size", 28))
    content_w = max(1, min(W - 2 * pd, cfg.get("foreground_max_width", 1000)))

    # Reserve the watermark's visible area. The content is centered in the
    # remaining safe rectangle instead of being centered over the full canvas.
    safe_top = int(outm)
    wm_geometry = watermark_geometry(H, cfg, N)
    safe_bottom = H - int(outm)
    if wm_geometry:
        safe_bottom = min(
            safe_bottom,
            int(wm_geometry["top"] - cfg.get("watermark_gap", 24)),
        )
    if safe_bottom <= safe_top:
        ap.error("el área segura vertical no deja espacio para el contenido")

    # Determine the smallest collage that the existing layout policy permits;
    # text is reduced before the image is allowed to escape the safe area.
    if N == 1:
        base_cell_w = max(1, content_w)
        base_cell_h = base_cell_w / ratio if ratio else base_cell_w
        min_collage_h = base_cell_h * 0.2
    elif style == "grid":
        grid_cols, grid_rows = grid_dims(N, W / H)
        natural_collage_h = content_w * grid_rows / grid_cols
        grid_gap_h = (grid_rows - 1) * g
        min_collage_h = grid_gap_h + max(1, (natural_collage_h - grid_gap_h) * 0.2)
    elif style == "adaptive":
        min_collage_h = max(1, g)
    else:
        min_collage_h = content_w * 0.2

    try:
        top_block = fit_block(probe, a.top, cfg)
        bottom_block = fit_block(probe, a.bottom, cfg)
    except ValueError as exc:
        ap.error(str(exc))

    top_size = top_block["size"]
    bot_size = bottom_block["size"]
    top_parts = top_block["parts"]
    bottom_parts = bottom_block["parts"]
    top_h = top_block["height"]
    bot_h = bottom_block["height"]

    for _ in range((top_size - mini) + (bot_size - mini) + 2):
        top_block = block_at_size(probe, a.top, cfg, top_size)
        bottom_block = block_at_size(probe, a.bottom, cfg, bot_size)
        top_parts = top_block["parts"]
        bottom_parts = bottom_block["parts"]
        top_h = top_block["height"]
        bot_h = bottom_block["height"]
        usable = safe_bottom - safe_top - top_h - bot_h - 2 * gap
        if usable >= min_collage_h:
            break
        if top_size <= mini and bot_size <= mini:
            ap.error(
                "el texto ocupa demasiado espacio vertical incluso con la fuente mínima; "
                "divide el titular o el contexto en menos líneas"
            )
        if top_size >= bot_size and top_size > mini:
            top_size -= 1
        elif bot_size > mini:
            bot_size -= 1

    usable = safe_bottom - safe_top - top_h - bot_h - 2 * gap
    cells = []   # (x, y, w, h) en px absolutos del canvas

    if N == 1:
        cols, rows = 1, 1
        cell_w = content_w
        cell_h = cell_w / ratio if ratio else cell_w
        collage_h = cell_h
        max_foreground_h = cfg.get("foreground_max_height", 0)
        target_h = min(usable, max_foreground_h) if max_foreground_h else usable
        if collage_h > target_h:
            scale = max(0.2, min(1.0, target_h / cell_h))
            cell_w *= scale
            cell_h = cell_w / ratio if ratio else cell_w
            collage_h = cell_h
        cw = cols * cell_w + (cols - 1) * g
        cx0 = (W - cw) // 2
        cells.append((cx0, 0, cell_w, cell_h))
        fit = "contain" if a.fit == "auto" else a.fit
    elif style == "grid":
        cols, rows = grid_dims(N, W / H)
        collage_w = content_w
        natural_collage_h = collage_w * rows / cols
        max_foreground_h = cfg.get("foreground_max_height", 0)
        target_h = min(usable, max_foreground_h) if max_foreground_h else usable
        grid_gap_h = (rows - 1) * g
        target_h = max(grid_gap_h + 1, target_h)
        collage_h = max(grid_gap_h + 1, min(natural_collage_h, target_h))
        cell_w = (collage_w - (cols - 1) * g) / cols
        cell_h = (collage_h - (rows - 1) * g) / rows
        cx0 = (W - collage_w) / 2
        for i in range(N):
            r_i, c_i = divmod(i, cols)
            cells.append((cx0 + c_i * (cell_w + g),
                          r_i * (cell_h + g),
                          cell_w, cell_h))
        fit = "cover" if a.fit == "auto" else a.fit
    elif style == "adaptive":
        stack_square_pair = (
            N == 2
            and H > W
            and all(aspect_bucket(src.size)[0] == "square" for src in srcs)
        )
        max_foreground_h = cfg.get("foreground_max_height", 0)
        # A stacked square pair has more unused horizontal space than a
        # regular mixed collage, so let it grow by 10% while staying safe.
        target_cap = max_foreground_h * 1.1 if stack_square_pair else max_foreground_h
        target_h = min(usable, target_cap) if target_cap else usable
        target_h = max(1, target_h)
        cells, collage_h = adaptive_layout(
            layout_srcs,
            W,
            content_w,
            target_h,
            g,
            stack_square_pair=stack_square_pair,
            equal_pair_cells=equal_pair_cells_active,
        )
        fit = "cover" if a.fit == "auto" else a.fit
    else:
        A_h, rects = LAYOUTS[(style, N)]
        collage_w = content_w
        collage_h = A_h * collage_w
        scale = 1.0
        max_foreground_h = cfg.get("foreground_max_height", 0)
        target_h = min(usable, max_foreground_h) if max_foreground_h else usable
        if collage_h > target_h:
            scale = max(0.2, min(1.0, target_h / collage_h))
        cw = collage_w * scale
        ch = collage_h * scale
        cx0 = (W - cw) / 2
        for (rx, ry, rw, rh) in rects:
            cells.append((cx0 + rx * cw, ry * ch, rw * cw, rh * ch))
        collage_h = ch
        fit = "cover" if a.fit == "auto" else a.fit

    group = top_h + gap + collage_h + gap + bot_h
    content_area_h = safe_bottom - safe_top
    y0 = safe_top + (content_area_h - group) // 2 + int(cfg.get("layout_shift_y", 0))
    ty = y0
    cy = ty + top_h + gap
    by = cy + collage_h + gap

    if ty < safe_top or by + bot_h > safe_bottom:
        ap.error("el contenido excede los márgenes seguros; reduce o divide el texto")

    for (x, y, w, h), src in zip(cells, layout_srcs):
        place_image(bg, src, x, y + cy, w, h, cfg, fit=fit, backend=backend)

    yy = ty
    for part in top_parts:
        draw_segments(bg, part["seg"], part["f"], yy, cfg, part["widths"])
        yy += part["h"] + line_gap
    yy = by
    for part in bottom_parts:
        draw_segments(bg, part["seg"], part["f"], yy, cfg, part["widths"])
        yy += part["h"] + line_gap
    draw_watermark(bg, cfg, N)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    suffix = out.suffix.lower()
    if suffix == ".png":
        bg.convert("RGBA").save(out, format="PNG", compress_level=0)
    elif suffix in {".jpg", ".jpeg"}:
        bg.convert("RGB").save(out, format="JPEG", quality=95, optimize=True)
    else:
        ap.error("la salida debe tener extension .png, .jpg o .jpeg")
    print(json.dumps({
        "output": str(out), "width": W, "height": H,
        "images": N, "ratio": round(ratio, 3), "style": style,
        "backend": backend,
        "resolution": a.resolution,
        "resolution_scale": resolution_scale,
        "orientations": [aspect_bucket(src.size)[0] for src in srcs],
        "top": {"y": ty, "font_size": top_size}, "bottom": {"y": by, "font_size": bot_size},
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
