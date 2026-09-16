#!/usr/bin/env python3
"""Render a reusable vertical Fortnite schedule card.

The sharp source image is intentionally omitted: only its blurred cover is
used behind the schedule panel. Flags are rendered from the bundled iOS-style
emoji font, enlarged and spaced so they never collide with the time column.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

CANVAS = (2160, 3840)
EMOJI_NATIVE_SIZE = 109
DEFAULT_SCHEDULE = Path(__file__).with_name("schedule.json")
DEFAULT_EMOJI = Path(__file__).with_name("assets") / "NotoColorEmoji.ttf"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="source image used only for the blurred background")
    parser.add_argument("--output", required=True, type=Path, help="final PNG path")
    parser.add_argument("--schedule", type=Path, default=DEFAULT_SCHEDULE, help="JSON file with flags and times")
    parser.add_argument("--version", default="V42.20")
    parser.add_argument("--date", default=dt.date.today().strftime("%d/%m/%Y"))
    parser.add_argument("--emoji-font", type=Path, default=DEFAULT_EMOJI)
    return parser.parse_args()


def load_schedule(path: Path) -> list[tuple[list[str], str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("schedule JSON must contain a non-empty rows list")
    result: list[tuple[list[str], str]] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not isinstance(row.get("flags"), list) or not isinstance(row.get("time"), str):
            raise ValueError(f"invalid schedule row {index}")
        flags = [str(flag) for flag in row["flags"]]
        if not flags or not row["time"].strip():
            raise ValueError(f"empty schedule row {index}")
        result.append((flags, row["time"]))
    return result


def centered_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, y: int, fill: str) -> None:
    box = draw.textbbox((0, 0), text, font=font, stroke_width=2)
    x = (CANVAS[0] - (box[2] - box[0])) // 2 - box[0]
    draw.text((x, y), text, font=font, fill=fill, stroke_width=2, stroke_fill="#000000")


def paste_scaled_emoji(layer: Image.Image, emoji_font: ImageFont.FreeTypeFont, text: str, center_x: int, y: int, target_height: int = 125) -> None:
    tile = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
    tile_draw = ImageDraw.Draw(tile)
    tile_draw.text((20, 20), text, font=emoji_font, embedded_color=True)
    bbox = tile.getbbox()
    if not bbox:
        raise ValueError(f"emoji has no drawable glyph: {text!r}")
    glyph = tile.crop(bbox)
    target_width = max(1, round(glyph.width * target_height / glyph.height))
    glyph = glyph.resize((target_width, target_height), Image.Resampling.LANCZOS)
    layer.alpha_composite(glyph, (round(center_x - target_width / 2), y))


def make_blurred_background(source_path: Path) -> Image.Image:
    with Image.open(source_path) as opened:
        source = opened.convert("RGB")
    width, height = CANVAS
    scale = max(width / source.width, height / source.height)
    resized = source.resize((round(source.width * scale), round(source.height * scale)), Image.Resampling.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    background = resized.crop((left, top, left + width, top + height))
    background = background.filter(ImageFilter.GaussianBlur(40))
    return ImageEnhance.Brightness(background).enhance(0.92).convert("RGBA")


def render(args: argparse.Namespace) -> dict:
    root = repo_root()
    barlow_path = root / ".hermes/skills/vertical-image-editor/assets/Barlow-BlackItalic.ttf"
    if not barlow_path.is_file():
        raise FileNotFoundError(f"missing project font: {barlow_path}")
    if not args.emoji_font.is_file():
        raise FileNotFoundError(f"missing emoji font: {args.emoji_font}")

    rows = load_schedule(args.schedule)
    background = make_blurred_background(args.source)
    layer = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    barlow = ImageFont.truetype(str(barlow_path), 100)
    header_font = ImageFont.truetype(str(barlow_path), 96)
    time_font = ImageFont.truetype(str(barlow_path), 100)
    small_font = ImageFont.truetype(str(barlow_path), 62)
    emoji = ImageFont.truetype(str(args.emoji_font), EMOJI_NATIVE_SIZE)

    title_prefix = "HORARIOS DE LA "
    title_word = "ACTUALIZACIÓN"
    prefix_box = draw.textbbox((0, 0), title_prefix, font=barlow, stroke_width=2)
    word_box = draw.textbbox((0, 0), title_word, font=barlow, stroke_width=2)
    title_width = (prefix_box[2] - prefix_box[0]) + (word_box[2] - word_box[0])
    title_x = (CANVAS[0] - title_width) // 2
    draw.text((title_x, 245), title_prefix, font=barlow, fill="#FFFFFF", stroke_width=2, stroke_fill="#000000")
    draw.text((title_x + prefix_box[2] - prefix_box[0], 245), title_word, font=barlow,
              fill="#FFD166", stroke_width=2, stroke_fill="#000000")

    panel = (160, 820, 2000, 3080)
    draw.rounded_rectangle(panel, radius=60, fill=(7, 12, 24, 246), outline=(255, 209, 102, 235), width=8)
    draw.text((300, 950), "⏰", font=emoji, embedded_color=True)
    draw.text((650, 970), "HORARIOS POR REGIÓN", font=header_font, fill="#FFFFFF", stroke_width=2, stroke_fill="#000000")
    draw.text((1660, 950), "🔥🍀", font=emoji, embedded_color=True)
    draw.line((300, 1200, 1860, 1200), fill=(255, 209, 102, 190), width=5)

    flag_center_x = 700
    flag_step = 205
    row_y = 1270
    for flags, time_text in rows:
        if len(flags) >= 7:
            groups = (flags[:4], flags[4:])
            for line_index, group in enumerate(groups):
                y = row_y + line_index * 145
                start_x = flag_center_x - ((len(group) - 1) * flag_step) // 2
                for flag_index, flag in enumerate(group):
                    paste_scaled_emoji(layer, emoji, flag, start_x + flag_index * flag_step, y, target_height=125)
            time_y = row_y + 65
            row_y += 300
        else:
            start_x = flag_center_x - ((len(flags) - 1) * flag_step) // 2
            for flag_index, flag in enumerate(flags):
                paste_scaled_emoji(layer, emoji, flag, start_x + flag_index * flag_step, row_y, target_height=125)
            time_y = row_y + 6
            row_y += 220
        box = draw.textbbox((0, 0), time_text, font=time_font, stroke_width=2)
        time_width = box[2] - box[0]
        draw.text((1600 - time_width // 2, time_y), time_text, font=time_font,
                  fill="#FFFFFF", stroke_width=2, stroke_fill="#000000")

    date_text = f"{args.version} · {args.date}"
    date_box = draw.textbbox((0, 0), date_text, font=small_font, stroke_width=2)
    date_y = min(row_y - 50, 2920)
    draw.text(((CANVAS[0] - (date_box[2] - date_box[0])) // 2, date_y), date_text, font=small_font,
              fill="#FFD166", stroke_width=2, stroke_fill="#000000")

    watermark = "CÓDIGO: KHETZALGG"
    watermark_font = ImageFont.truetype(str(barlow_path), 92)
    watermark_box = draw.textbbox((0, 0), watermark, font=watermark_font)
    watermark_x = (CANVAS[0] - (watermark_box[2] - watermark_box[0])) // 2 - watermark_box[0]
    watermark_y = CANVAS[1] - 290 - watermark_box[3]
    draw.text((watermark_x, watermark_y), watermark, font=watermark_font, fill=(255, 255, 255, 125),
              stroke_width=1, stroke_fill=(0, 0, 0, 62))

    result = Image.alpha_composite(background, layer)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_name(args.output.stem + ".tmp.png")
    result.save(temp, format="PNG", optimize=True, compress_level=9)
    os.replace(temp, args.output)
    return {
        "output": str(args.output),
        "width": result.width,
        "height": result.height,
        "rows": len(rows),
        "background": "blurred-only",
        "emoji_font": str(args.emoji_font),
        "flag_height": 125,
        "flag_step": 205,
        "time_center": 1600,
    }


if __name__ == "__main__":
    arguments = parse_args()
    print(json.dumps(render(arguments), ensure_ascii=False))
