---
name: vertical-image-editor
description: "Usar obligatoriamente para editar una noticia o imagen de Fortnite desde un enlace de X/Twitter o archivos adjuntos cuando el resultado sea vertical, 9:16, story, historia o 1080x1920. Descarga todos los medios, compone y valida un PNG."
version: 3.0.0
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
2. Ejecutar `edit_link.py` con el preset vertical, o `compose_image.py` sobre
   los archivos de `prepare_link.py` si ya se descargaron. Mantener el orden de
   todos los medios y usar como máximo 24 imágenes; la descarga no es el final.
3. Verificar el resultado con `verify_image.py`.
4. Entregar el PNG original como documento cuando el canal lo permita; no usar
   una foto comprimida como sustituto.

`SKILL_DIR` es la ruta absoluta de esta carpeta. Estos comandos no dependen de
`bin/` ni de `barlow_font/` de la raíz.

```text
python "<SKILL_DIR>/scripts/edit_link.py" "<URL>" "<OUTPUT>.png" --top "<TITULAR>" --bottom "<CONTEXTO>" --format 9:16 --fit auto --style auto --backend auto --max-images 24 --preset "<SKILL_DIR>/references/presets/fortnite_vertical_image.json"
python "<SKILL_DIR>/scripts/verify_image.py" "<OUTPUT>.png" --format PNG --mode RGBA --width 1080 --height 1920
```

Para imágenes locales, usar `scripts/compose_image.py` en lugar de
`edit_link.py` y pasar una o más imágenes antes de la salida.

## Reglas visuales

- Salida 1080×1920, fondo blur cover y sujeto sin deformar.
- Barlow Black Italic, texto centrado, márgenes seguros y máximo dos colores de
  acento.
- Mantener `CÓDIGO: KHETZALGG` como marca de agua, sin créditos ni autores.
- Una tarjeta por enlace; no mezclar medios ni textos de enlaces distintos.
- No publicar en Instagram sin una orden explícita.

## Paquete autocontenido

La skill incluye `scripts/compose_image.py`, `edit_link.py`, `prepare_link.py`, `fetch_media.py`,
`render_backend.py`, `runtime_config.py`, `verify_image.py`, el preset vertical
y `assets/Barlow-BlackItalic.ttf` y su licencia `assets/OFL.txt`.
