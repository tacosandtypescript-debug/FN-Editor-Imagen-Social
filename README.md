# EditImg — Editor de tarjetas verticales de Fortnite

Proyecto completo para crear tarjetas 1080×1920 a partir de imágenes o publicaciones de X.

## Incluye

- `bin/compose_image.py`: compositor principal Pillow para 1 o más imágenes.
- `bin/fetch_media.py`: descarga en orden todas las imágenes de un post de X/Twitter.
- `bin/edit_link.py`: une descarga + composición en un solo comando.
- `skills/media/vertical-image-editor/scripts/compose_image.py`: entrada
  compatible con la skill que delega en el compositor principal.
- Presets JSON con tipografía, paleta, sombras, márgenes y marca de creador.
- Scripts auxiliares de composición y renderizado de tablas/banderas.
- Skills y reglas de edición en `skills/`.
- Familia tipográfica Barlow incluida en `barlow_font/`.
- Material de trabajo y ejemplos en `jobs/`.

## Requisitos

- Linux
- Python 3.10+
- Pillow
- `python-xlib` (solo para `bin/clipboard_server.py`)
- FFmpeg (para extraer fotogramas de vídeos)
- Google Chrome/Chromium (solo para renderizar tablas HTML con banderas)
- Fuente Barlow incluida o instalada en el sistema

Instalación mínima:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Uso

```bash
python3 bin/compose_image.py imagen.jpg salida.png \
  --top 'TITULAR {PALABRA|8B3DFF}' \
  --bottom 'CONTEXTO DE LA NOTICIA' \
  --preset bin/preset.json
```

Para un lote de imágenes, se conserva el orden y el modo `auto` clasifica cada
fuente: horizontal → celda 16:9, cercana a cuadrada → 1:1 y vertical → 9:16.
Las celdas se recortan con `cover` sin deformar la imagen. En cualquier
collage de 2 o más imágenes, `auto` usa `adaptive`; los estilos tradicionales
se pueden forzar explícitamente.

Cuando hay exactamente dos imágenes cuadradas en un lienzo vertical, se
apilan una arriba de la otra para aprovechar mejor el espacio. En un lienzo
cuadrado o apaisado se mantienen lado a lado.

```bash
python3 bin/compose_image.py img1.jpg img2.jpg img3.jpg salida.png \
  --top 'NOVEDADES {FORTNITE|8B3DFF}' \
  --bottom 'CONTEXTO DE LA NOTICIA' \
  --format 9:16 --fit auto --preset bin/preset.json
```

`--format` acepta proporciones como `1:1`, `4:5`, `16:9` y `9:16` (también
`W:H`, `W x H` o `W/H`). La salida PNG conserva el tamaño equivalente a 1080
px en el lado corto y se guarda sin compresión PNG.

También se puede pasar directamente el enlace de un post de X/Twitter. Se
descargan todas sus imágenes y se componen en el mismo orden:

```bash
python3 bin/edit_link.py 'https://x.com/usuario/status/123456789' salida.png \
  --top 'TITULAR' --bottom 'CONTEXTO' \
  --format 9:16 --preset bin/preset.json
```

El flujo admite además una URL directa de imagen. Por seguridad, limita cada
descarga a 25 MB y el lote a 24 imágenes salvo que se cambie `--max-images`.

La fuente Barlow se resuelve desde `barlow_font/` y el preset funciona aunque el
comando se ejecute desde otro directorio.

## Pruebas

```bash
python3 -m unittest discover -s tests -v
```

## Reglas visuales

- Lienzo vertical 1080×1920.
- Márgenes seguros y texto centrado.
- Barlow Black Italic.
- Paleta Halloween: morado, naranja, magenta y dorado.
- Marca fija: `CÓDIGO: KHETZALGG`.
- Un color por palabra; en el estilo habitual: una palabra arriba y dos abajo.
- Las tablas de banderas/horarios se tratan como un bloque único centrado.
- En modo `adaptive`, horizontal significa ratio ≥ 1.25, cuadrada significa
  0.8–1.25 y vertical significa ratio ≤ 0.8.

## Telegram / publicación

La entrega de imágenes debe hacerse como documento PNG original, nunca como foto comprimida. La publicación a Instagram usa el sistema externo `igpub`; sus credenciales y configuración no se incluyen en este repositorio.

## Seguridad

No se incluyen tokens, contraseñas, archivos de sesión ni entornos virtuales. Configura cualquier credencial mediante variables de entorno o archivos locales ignorados por Git.
