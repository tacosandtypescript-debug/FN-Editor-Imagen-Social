---
name: square-image-editor
description: "Use when editing Fortnite news images into square cards."
version: 1.1.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [imagenes, fortnite, cuadrado, 1:1, collage, telegram]
---

# Square Image Editor — tarjetas cuadradas de noticias Fortnite

Crea tarjetas 1080×1080 (1:1) con el mismo flujo visual del editor vertical:
fondo blur, imágenes sin deformar, recorte adaptativo, sombra diagonal ligera,
texto centrado y marca de agua `CÓDIGO: KHETZALGG`.

## Activación

Usar cuando Isaac pida una tarjeta cuadrada, un post para feed en 1:1 o diga
que quiere trabajar con el formato cuadrado. También se activa al recibir
imágenes sueltas con `1:1` o `cuadrado`.

## Entrega

- Entregar el PNG final como documento mediante `sendDocument`.
- No usar `sendPhoto`, no comprimir ni convertir a JPEG.
- Verificar que el resultado sea PNG RGBA de 1080×1080 antes de entregarlo.

## Comando

```bash
python3 skills/media/square-image-editor/scripts/compose_image.py \
  img1.jpg [img2..imgN] salida.png \
  --top 'TITULAR {CLAVE|8B3DFF}' \
  --bottom 'CONTEXTO · 03/09' \
  --fit contain \
  [--max-images 24] [--background fondo.jpg] \
  --preset skills/media/square-image-editor/references/presets/fortnite_square_image.json
```

El último argumento es la salida. Una imagen crea una tarjeta simple; dos o
más crean un collage. El wrapper cuadrado comparte toda la lógica con
`bin/compose_image.py`, por lo que las correcciones del compositor se aplican
a ambos formatos.

Para editar directamente desde un enlace de X/Twitter:

```bash
python3 bin/edit_link.py 'https://x.com/USUARIO/status/ID' salida.png \
  --format 1:1 \
  --top 'TITULAR' --bottom 'CONTEXTO · 03/09' \
  --preset skills/media/square-image-editor/references/presets/fortnite_square_image.json
```

## Acomodo y estilo

- `auto` usa `adaptive` en cualquier collage de 2 o más imágenes.
- Ratio ≥1.25 → celda horizontal 16:9.
- Ratio 0.8–1.25 → celda cuadrada 1:1.
- Ratio ≤0.8 → celda vertical 9:16.
- En un lienzo cuadrado, dos imágenes cuadradas se colocan lado a lado.
- El preset cuadrado activa `equal_pair_cells` para cualquier pareja de
  imágenes: ambos paneles son cuadrados del mismo tamaño y quedan en una sola
  fila. Usar `--fit contain` cuando las fuentes tengan proporciones distintas;
  la fuente completa queda centrada dentro de su panel, con relleno del fondo,
  sin recortarla ni deformarla.
- Las imágenes mixtas conservan su orden y cada una recibe su propia forma
  cuando no está activa la pareja de celdas iguales.
- El recorte por defecto es `cover` centrado, sin deformar; usar `--fit contain`
  únicamente si se necesita conservar toda la imagen.
- Los layouts editoriales explícitos admiten 2–4 imágenes; una cantidad distinta
  produce un error claro.

## Reglas visuales

- Mantener márgenes seguros y texto centrado.
- Usar como máximo dos colores de acento por tarjeta; el resto del texto va en
  blanco.
- Aplicar una sombra exterior corta y suave, desplazada ligeramente a la
  izquierda y abajo en diagonal (blur 28, offset -8×12, opacidad 48).
- No añadir créditos ni nombres de autores dentro de la imagen.
- Si no se proporciona texto, redactar un titular corto de 3–7 palabras y un
  contexto breve.

## Archivos

- `scripts/compose_image.py`: wrapper cuadrado del compositor común.
- `references/presets/fortnite_square_image.json`: preset 1080×1080.
- `bin/edit_link.py`: descarga el post y compone todos sus medios en orden.
