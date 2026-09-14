---
name: vertical-image-editor
description: "Usar obligatoriamente para editar una noticia o imagen de Fortnite desde un enlace de X/Twitter o archivos adjuntos cuando el resultado sea vertical, 9:16, story, historia o 2160x3840 4K. Descarga todos los medios, compone y valida un PNG."
version: 3.3.0
author: Isaac
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [fortnite, imagenes, vertical, 9:16, story, historia, x, telegram]
    related_skills: [fortnite-image-editor, square-image-editor]
---

# Vertical Image Editor

Modo vertical del editor de imágenes. Si Isaac pega un enlace y pide editarlo,
descargar todos los medios, componer la tarjeta y entregar el resultado; la
descarga no es el final del trabajo.

## Flujo

1. Leer el texto del post de X con las herramientas disponibles. Si hace falta,
   ejecutar `scripts/prepare_link.py` para descargar los medios y obtener
   `post_text` en JSON. Si Isaac no da titular y contexto, redactar un titular
   español de 3–7 palabras y una línea breve basados en el contenido real; no
   inventar datos.
2. Si Isaac envía varios enlaces en el mismo mensaje, ejecutar
   `scripts/prepare_batch.py` con todos los enlaces en el orden recibido. Cada
   `sources[i]` es una publicación independiente y genera una salida propia;
   nunca mezclar fuentes.
3. Ejecutar `edit_link.py` para una fuente, o `compose_image.py` una vez por
   cada `sources[i].images`. Si una publicación trae 1, 2 o 3 imágenes, esas
   imágenes se mantienen juntas en una única tarjeta vertical. Mantener el
   orden, usar como máximo 24 medios por publicación y `--fit contain` para
   evitar zoom y recortes de capturas.
4. Ejecutar `verify_image.py` por cada salida.
5. Entregar un PNG 4K por publicación como documento independiente y en orden;
   en Telegram usar `sendDocument`, no `sendPhoto` ni una previsualización
   comprimida.

Para un lote, el comando de descarga es:

```text
python "<SKILL_DIR>/scripts/prepare_batch.py" "<WORK_DIR>" "<URL_1>" "<URL_2>" --max-images 24
```

Para varios enlaces, componer una imagen por cada grupo `sources[i].images`,
con su propio titular, contexto, fecha y caption. El compositor reserva las
zonas seguras para que el texto superior e inferior siempre aparezca.

`SKILL_DIR` es la ruta absoluta de esta carpeta. Estos comandos no dependen de
`bin/` ni de `barlow_font/` de la raíz.

```text
python "<SKILL_DIR>/scripts/edit_link.py" "<URL>" "<OUTPUT>.png" --top "<TITULAR>" --bottom "<CONTEXTO>" --format 9:16 --resolution 4k --fit auto --style auto --backend auto --max-images 24 --preset "<SKILL_DIR>/references/presets/fortnite_vertical_image.json"
python "<SKILL_DIR>/scripts/verify_image.py" "<OUTPUT>.png" --format PNG --mode RGBA --width 2160 --height 3840
```

Para imágenes locales, usar `scripts/compose_image.py` en lugar de
`edit_link.py` y pasar una o más imágenes antes de la salida.

## Reglas visuales

- Salida 4K 2160×3840, fondo blur cover y sujeto sin deformar.
- Barlow Black Italic, texto centrado, márgenes seguros y máximo dos colores de
  acento.
- Mantener `CÓDIGO: KHETZALGG` como marca de agua, sin créditos ni autores.
- Texto blanco por defecto; solo colorear palabras semánticamente importantes,
  nunca `de`, `la`, `los`, `a`, `o`, `y`, `que` o `se`. La CLI rechaza esos
  resaltados y más de dos colores.
- Para el caption de cada publicación, devolver su título + fecha + exactamente
  cinco hashtags únicos, incluyendo `#khetzalgg`. Consultar TikTok Creative
  Center para los otros cuatro hashtags del día; no inventar viralidad si no se
  puede verificar. Los hashtags van fuera de la imagen.
- No publicar en Instagram sin una orden explícita.

## Comunicación

- Usar un único estado persistente en español para toda la petición y actualizar
  el mismo mensaje si la superficie lo permite (`⏳ Preparando 4…` → `🔄 2/4…`
  → `✅ 4/4 listas`). No enviar un mensaje por cada paso interno.

## Paquete autocontenido

La skill incluye `scripts/compose_image.py`, `edit_link.py`, `prepare_link.py`, `prepare_batch.py`, `fetch_media.py`,
`render_backend.py`, `runtime_config.py`, `verify_image.py`, los presets vertical,
cuadrado y horizontal (incluido el soporte `--format auto`)
y `assets/Barlow-BlackItalic.ttf` y su licencia `assets/OFL.txt`.
