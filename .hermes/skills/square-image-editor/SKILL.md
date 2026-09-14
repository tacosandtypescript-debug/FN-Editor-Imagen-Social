---
name: square-image-editor
description: "Usar obligatoriamente para editar una noticia o imagen de Fortnite desde un enlace de X/Twitter o archivos adjuntos cuando el resultado sea cuadrado, 1:1, feed, post cuadrado o 2160x2160 4K. Descarga todos los medios, compone y valida un PNG."
version: 3.1.0
author: Isaac
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [fortnite, imagenes, cuadrado, 1:1, feed, post, x, telegram]
    related_skills: [fortnite-image-editor, vertical-image-editor]
---

# Square Image Editor

Modo cuadrado del editor de imágenes. Si Isaac pega un enlace y pide editarlo,
descargar todos los medios, componer la tarjeta y entregar el resultado; la
descarga no es el final del trabajo.

## Flujo

1. Leer el texto del post de X con las herramientas disponibles. Si hace falta,
   ejecutar `scripts/prepare_link.py` para descargar los medios y obtener
   `post_text` en JSON. Si Isaac no da titular y contexto, redactar un titular
   español de 3–7 palabras y una línea breve basados en el contenido real; no
   inventar datos.
2. Si Isaac envía varios enlaces en el mismo mensaje, ejecutar
   `scripts/prepare_batch.py` con todos los enlaces en el orden recibido. Es un
   único carrusel: leer todos los `sources[].post_text`, crear un solo titular
   general de 3–12 palabras y una sola línea compartida con contexto y fecha.
   No crear una tarjeta/título independiente por enlace.
3. Ejecutar `edit_link.py` con el preset cuadrado para una fuente, o
   `compose_image.py` una sola vez sobre todos los paths de `prepare_batch.py`
   para un lote. Mantener el orden y usar como máximo 24 imágenes; en lotes
   usar `--fit contain` para evitar zoom y recortes de capturas.
4. Ejecutar `verify_image.py`.
5. Entregar el PNG 4K original como documento; en Telegram usar `sendDocument`,
   no `sendPhoto` ni una previsualización comprimida.

Para un lote, el comando de descarga es:

```text
python "<SKILL_DIR>/scripts/prepare_batch.py" "<WORK_DIR>" "<URL_1>" "<URL_2>" --max-images 24
```

Después, componer una sola imagen con todos los paths del manifiesto y un único
`--top` y `--bottom` no vacíos. El compositor reserva las zonas seguras para
que el texto superior e inferior siempre aparezca.

`SKILL_DIR` es la ruta absoluta de esta carpeta. Estos comandos no dependen de
`bin/` ni de `barlow_font/` de la raíz.

```text
python "<SKILL_DIR>/scripts/edit_link.py" "<URL>" "<OUTPUT>.png" --top "<TITULAR>" --bottom "<CONTEXTO>" --format 1:1 --resolution 4k --fit auto --style auto --backend auto --max-images 24 --preset "<SKILL_DIR>/references/presets/fortnite_square_image.json"
python "<SKILL_DIR>/scripts/verify_image.py" "<OUTPUT>.png" --format PNG --mode RGBA --width 2160 --height 2160
```

Para imágenes locales, usar `scripts/compose_image.py` en lugar de
`edit_link.py` y pasar una o más imágenes antes de la salida.

## Reglas visuales

- Salida 4K 2160×2160, fondo blur cover y sujeto sin deformar.
- En una pareja de imágenes, usar paneles cuadrados iguales en una fila;
  `contain` conserva completa una fuente vertical o mixta.
- Barlow Black Italic, texto centrado, márgenes seguros y máximo dos colores de
  acento.
- Mantener `CÓDIGO: KHETZALGG` como marca de agua, sin créditos ni autores.
- Texto blanco por defecto; solo colorear palabras semánticamente importantes,
  nunca `de`, `la`, `los`, `a`, `o`, `y`, `que` o `se`. La CLI rechaza esos
  resaltados y más de dos colores.
- Para el caption del carrusel, devolver título general + fecha + exactamente
  cinco hashtags únicos, incluyendo `#khetzalgg`. Consultar TikTok Creative
  Center para los otros cuatro hashtags del día; no inventar viralidad si no se
  puede verificar. Los hashtags van fuera de la imagen.
- No publicar en Instagram sin una orden explícita.

## Paquete autocontenido

La skill incluye `scripts/compose_image.py`, `edit_link.py`, `prepare_link.py`, `prepare_batch.py`, `fetch_media.py`,
`render_backend.py`, `runtime_config.py`, `verify_image.py`, el preset cuadrado
y `assets/Barlow-BlackItalic.ttf` y su licencia `assets/OFL.txt`.
