---
name: fortnite-image-editor
description: "Skill principal para editar imágenes de noticias Fortnite: activar cuando el usuario pegue un enlace de X/Twitter, una URL directa de imagen o adjunte imágenes. Descarga todos los medios, decide vertical 9:16 o cuadrada 1:1, compone, valida y entrega el PNG."
version: 3.1.0
author: Isaac
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [fortnite, imagenes, x, twitter, enlace, link, vertical, cuadrado, 9:16, 1:1, telegram]
    related_skills: [vertical-image-editor, square-image-editor, editar-publicar-instagram]
---

# Fortnite Image Editor — dispatcher

Esta es la skill de entrada para cualquier petición de edición de imágenes.
Cuando Isaac pega un enlace y pide editarlo, el enlace es una orden de trabajo:
no responder solo con un resumen ni detenerse tras descargar los medios.

## Decidir el formato

| Pedido de Isaac | Modo | Preset | Salida |
|---|---|---|---|
| `cuadrada`, `1:1`, `feed`, `post cuadrado` | square | `references/presets/fortnite_square_image.json` | 2160×2160 PNG |
| `vertical`, `9:16`, `story`, `historia` | vertical | `references/presets/fortnite_vertical_image.json` | 2160×3840 PNG |
| Sin formato explícito | vertical | vertical | 2160×3840 PNG |

No cargar los dos modos para una misma tarjeta. Si Isaac pide varios formatos,
crear una salida independiente por formato.

## Procedimiento obligatorio

1. Identificar si es un post de `x.com`/`twitter.com` o una URL directa de
   imagen. En un post, obtener el texto visible antes de redactar el titular.
   Si se necesita hacerlo desde el paquete, ejecutar primero
   `scripts/prepare_link.py`; su JSON devuelve `post_text` y todos los medios
   descargados en orden.
2. Si Isaac envía varios enlaces en el mismo mensaje, ejecutar
   `scripts/prepare_batch.py` con todos ellos en el orden recibido. El JSON
   devuelve `sources[].post_text`, la agrupación por enlace y `images` ya
   ordenadas. Es un solo carrusel: redactar un único titular general que
   abarque todos los temas y un único contexto/fecha; no generar un título por
   cada enlace.
3. Completar la edición en la misma petición: ejecutar el `edit_link.py` de
   esta skill (si ya se obtuvo el texto con una herramienta) o ejecutar
   `compose_image.py` sobre los archivos que devolvió `prepare_link.py`. Nunca
   dejar la tarea en “descarga terminada”. Conserva el límite de 24 medios;
   para vídeos o GIFs, el descargador usa la miniatura disponible.
4. Si Isaac no da textos, redactar el titular general en español (3–12 palabras
   para un lote; 3–7 para una sola fuente) y una línea de contexto con la fecha,
   basados solo en los posts. Si no se puede leer una fuente, avisar; no
   inventar la noticia.
5. Ejecutar `verify_image.py` con formato PNG, modo RGBA y dimensiones 4K del
   modo: `2160×3840` vertical o `2160×2160` cuadrado.
6. Entregar inmediatamente la tarjeta PNG 4K como documento/archivo original
   cuando el canal lo permita. En Telegram usar `sendDocument` (archivo), no
   `sendPhoto` (foto) ni una previsualización comprimida.

`SKILL_DIR` significa la ruta absoluta de la carpeta que contiene este
`SKILL.md`. Los scripts y presets de esta skill son autocontenidos; no uses los
archivos equivalentes de la raíz del repositorio.

## Comandos

Usa el intérprete Python disponible en el entorno de Hermes (`python` en
Windows; `python3` normalmente en Linux/macOS).

### Vertical 9:16

```text
# Opción A: el agente ya obtuvo el texto del post con sus herramientas.
python "<SKILL_DIR>/scripts/edit_link.py" "<URL>" "<OUTPUT>.png" --top "<TITULAR>" --bottom "<CONTEXTO>" --format 9:16 --resolution 4k --fit auto --style auto --backend auto --max-images 24 --preset "<SKILL_DIR>/references/presets/fortnite_vertical_image.json"

# Opción B: obtener texto + medios desde el paquete y componer con los paths del JSON.
python "<SKILL_DIR>/scripts/prepare_link.py" "<URL>" "<WORK_DIR>" --max-images 24
python "<SKILL_DIR>/scripts/compose_image.py" "<MEDIA_01>" "<OUTPUT>.png" --top "<TITULAR>" --bottom "<CONTEXTO>" --format 9:16 --resolution 4k --fit auto --style auto --backend auto --max-images 24 --preset "<SKILL_DIR>/references/presets/fortnite_vertical_image.json"
```

Para varios enlaces en un solo carrusel:

```text
python "<SKILL_DIR>/scripts/prepare_batch.py" "<WORK_DIR>" "<URL_1>" "<URL_2>" --max-images 24
python "<SKILL_DIR>/scripts/compose_image.py" "<TODOS_LOS_PATHS_DEL_JSON>" "<OUTPUT>.png" --top "<TITULAR_GENERAL>" --bottom "<CONTEXTO Y FECHA>" --format 9:16 --resolution 4k --fit contain --style auto --backend auto --max-images 24 --preset "<SKILL_DIR>/references/presets/fortnite_vertical_image.json"
```

### Cuadrada 1:1

```text
python "<SKILL_DIR>/scripts/edit_link.py" "<URL>" "<OUTPUT>.png" --top "<TITULAR>" --bottom "<CONTEXTO>" --format 1:1 --resolution 4k --fit auto --style auto --backend auto --max-images 24 --preset "<SKILL_DIR>/references/presets/fortnite_square_image.json"
```

Para varios enlaces en un carrusel cuadrado, usar `prepare_batch.py` y una
sola llamada a `compose_image.py` con todos los paths del JSON, `--resolution 4k`,
`--fit contain` y el preset cuadrado.

Para archivos locales, sustituir `edit_link.py` por
`compose_image.py` y pasar una o más imágenes antes de la salida.

### Verificación

```text
python "<SKILL_DIR>/scripts/verify_image.py" "<OUTPUT>.png" --format PNG --mode RGBA --width <2160> --height <3840-or-2160>
```

## Reglas visuales

- Mantener la composición visual existente: fondo blur cover, sujeto sin
  deformar, Barlow Black Italic, texto centrado y sombra ligera.
- Vertical: márgenes seguros, marca `CÓDIGO: KHETZALGG`, titular arriba y
  contexto abajo; máximo dos colores de acento.
- Cuadrada: dos imágenes cuadradas en una fila; si las fuentes son mixtas,
  conservarlas completas con `contain` cuando sea necesario.
- No añadir créditos ni nombres de autores dentro de la imagen.
- El texto debe ir siempre en las zonas superior e inferior. No usar una cadena
  vacía para `--top` o `--bottom`; la CLI la rechaza.
- Usar color solo en palabras importantes mediante `{PALABRA|HEX}`. No marcar
  `de`, `la`, `los`, `a`, `o`, `y`, `que`, `se` ni otras palabras funcionales;
  la CLI rechaza esos marcadores y más de dos colores.
- Para cada lote, devolver un único caption con el titular general, la fecha y
  exactamente cinco hashtags únicos, incluyendo `#khetzalgg`. Consultar el
  [TikTok Creative Center](https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en),
  preferentemente con región España e industria Gaming/Fortnite, para elegir
  los otros cuatro hashtags del día y no afirmar viralidad si no se pudo verificar.
- Si el usuario pide solo título y hashtags, devolver únicamente esas dos cosas;
  no generar una publicación larga.

## Errores y publicación

- Si falla la descarga, informar del enlace concreto y no mezclarlo con otro
  trabajo pendiente.
- No publicar en Instagram solo porque el usuario pidió editar. Publicar solo
  ante una orden explícita y usando `editar-publicar-instagram`.
- Después de modificar una skill, verificarla en una sesión nueva o con `/reset`.

## Archivos que deben viajar con la skill

`SKILL.md`, `scripts/compose_image.py`, `scripts/edit_link.py`,
`scripts/prepare_link.py`, `scripts/prepare_batch.py`, `scripts/fetch_media.py`, `scripts/render_backend.py`,
`scripts/runtime_config.py`, `scripts/verify_image.py`, ambos presets y
`assets/Barlow-BlackItalic.ttf` y `assets/OFL.txt` forman el paquete completo.
