---
name: vertical-image-editor
description: "Use when editing Fortnite news images into vertical cards."
version: 2.1.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [imagenes, fortnite, vertical, 9:16, blur, collage, telegram]
---

# Vertical Image Editor — tarjetas verticales de noticias Fortnite

Crea tarjetas verticales 1080x1920 (9:16) con fondo blur, imagen(es) sin
deformar, sombra difusa diagonal y texto con palabras de color.

## Activacion

Usar cuando Isaac pase un tweet/link con imagen(es) de noticias (Fortnite u
otras) y pida editarlas, o diga "edita", "practica", "otro", etc. Tambien al
recibir imagenes sueltas con instrucciones.

## Entrega en Telegram

- Entregar siempre el resultado final con `sendDocument` como PNG original.
- No comprimir, convertir a JPEG, reducir, deformar ni usar `sendPhoto`.
- Mantener la resolución original del resultado, normalmente 1080x1920.
- Usar nombres de salida con extensión `.png` y verificar formato, dimensiones y modo antes de entregar.

Estas reglas solo se aplican cuando una publicación incluya grupos de banderas de países junto con horarios en una o más tablas o columnas. No alteran el funcionamiento normal de las demás publicaciones.

- Conservar todos los países, banderas y horarios exactamente como aparecen.
- Si hay dos columnas, tratarlas como un solo bloque: centrar el bloque completo y alinear su punto medio con el centro exacto de la imagen.
- Escalar el bloque completo proporcionalmente respecto a la imagen principal superior, sin deformarlo, estirarlo ni escalar una columna por separado.
- Mantener legibles los encabezados, las banderas y los horarios.
- No cambiar textos, colores, tipografía, fondo, diseño ni ningún otro elemento.
- No recortar ni sustituir banderas por abreviaturas si es posible renderizarlas con la fuente de emojis disponible.

- Mantener una cola FIFO de enlaces recibidos durante la sesión.
- Solo un enlace puede estar en procesamiento activo a la vez.
- Si llega otro enlace mientras se procesa uno, añadirlo al final sin interrumpir
  ni reiniciar el trabajo activo.
- Al terminar: entregar inmediatamente el resultado del enlace actual y empezar
  automáticamente el siguiente pendiente.
- No mezclar imágenes, textos ni resultados entre enlaces de la cola.
- Si el enlace actual falla, informar el fallo, marcarlo como fallido y continuar
  con el siguiente sin detener la cola.

1. Obtener el tweet via `api.vxtwitter.com/USUARIO/status/ID` (o fxtwitter).
2. Extraer y bajar **todas** las imágenes en el orden original; para medios de
   X pedir `?name=orig` (o capturar frame si es video: ffmpeg). No truncar un
   carrusel a cuatro imágenes: el límite operativo por defecto es 24.
3. Editar en espanol con titular inventado por el bot si Isaac no indica texto.
   NO esperar aprobacion cuando Isaac esta en modo "practica/entrenamiento":
   generar directo en estilo Directo (arriba hecho, abajo fecha/contexto).
4. Entregar las tarjetas con MEDIA y un titulo + hashtags (1 caption por
   carrusel si son varias).

## Comando

```bash
python3 skills/media/vertical-image-editor/scripts/compose_image.py img1.jpg [img2..imgN] salida.png \
  --top 'TEXTO {CLAVE|FFD700}' --bottom 'CONTEXTO · 03/09' \
  [--format 9:16] [--fit auto|cover|contain] \
  [--style auto|adaptive|grid|bento|mosaico|puzzle|jerarquico|asimetrico] \
  --preset skills/media/vertical-image-editor/references/presets/fortnite_vertical_image.json
```

El ULTIMO argumento es la salida. 1 imagen = tarjeta simple; 2 o más = collage.
La ruta de `scripts/compose_image.py` es una entrada compatible; la lógica vive
en `bin/compose_image.py` para evitar que ambas implementaciones diverjan.

Para trabajar directamente desde un enlace de X/Twitter y tomar todas las
imágenes del post en su orden original:

```bash
python3 bin/edit_link.py 'https://x.com/USUARIO/status/ID' salida.png \
  --top 'TITULAR' --bottom 'CONTEXTO · 03/09' \
  --format 9:16 --preset skills/media/vertical-image-editor/references/presets/fortnite_vertical_image.json
```

## Reglas de estilo (aprobadas por Isaac)

- **Margenes seguros SIEMPRE**: 90 px arriba/abajo y 60 px a cada lado
  (texto e imagenes dentro de la zona segura; nada pegado a bordes).
- **Sombra de imagen**: UNA sola sombra exterior corta y ligera, desplazada
  ligeramente a la izquierda y abajo en diagonal (blur 28, desplazamiento
  -8x12, opacidad 48), con máscara expandida antes del blur para que no quede
  cuadrada.
- **Marca de agua**: añadir siempre `CÓDIGO: KHETZALGG` centrado en la zona
  inferior, separado del borde (145 px desde abajo), en blanco semitransparente
  (opacidad 125, tamaño 46). No pegarlo al límite ni ponerlo sobre el titular.
- **Marca de agua en collages (2+ imagenes) o composiciones altas**: cuando
  haya dos imágenes o los elementos principales ocupen demasiado espacio,
  colocar `CÓDIGO: KHETZALGG` en uno de los laterales según dónde interfiera
  menos con el diseño. Reglas:
  - Fuera de la zona segura, en el espacio lateral entre la zona segura y el
    borde del lienzo (franja 0..60 o 1020..1080).
  - Avanzada hacia la imagen: borde interior a unos 20 px dentro del límite de
    la zona segura, para que quede cerca del contenido sin cortarse.
  - Siempre dentro del lienzo 1080x1920: nunca cortado ni fuera de la imagen.
  - Puede rotarse la palabra completa 90° para acomodarla verticalmente; NO
    rotar letras individualmente; NO poner letras una debajo de otra; mantener
    khetzalgg como una sola pieza de texto legible.
  - Elegir automáticamente izquierda o derecha según el lado con más espacio y
    que tape menos contenido (heurística: menor contraste en la franja lateral).
  - Tarjetas simples (1 imagen) mantienen la marca centrada abajo.
- **Sombra de texto**: una sombra negra independiente, pequeña y difuminada
  (blur 14, desplazamiento 3x5, opacidad 72), sin doble contorno.
- **Sin creditos**: nunca poner "VIA @cuenta" ni el autor dentro de la imagen.
- **Tamaño uniforme**: todas las líneas del titular y del contexto deben usar el mismo tamaño de tipografía. Si una línea no cabe, dividir la frase en líneas más cortas antes de reducir el tamaño. Después, comprobar que el grupo completo queda dentro de los 90 px de margen superior e inferior.
  (outline) 6 px. Si un titular no cabe en una línea con letra grande, partirlo
  en 2 líneas separadas por `\n` en `--top` (cada línea conserva su color y se
  centra).
- **Color de texto**: mantener el texto mayormente blanco y usar como máximo dos
  colores de acento por tarjeta: normalmente una palabra arriba y otra abajo.
  Elegir los acentos por contraste con el fondo; no juntar los cuatro colores
  de la paleta en una misma tarjeta salvo que Isaac lo pida explícitamente.
- **Alineación**: centrar horizontalmente cada línea de texto, tanto el titular como el contexto inferior; nunca dejar líneas corridas hacia la izquierda.
- Texto auto-encogido para caber siempre (nunca se corta).
- Fondo: cover blur de la primera imagen, nunca estirar. Sujeto contain.

- **Publicaciones de tienda**: si el tweet es una lista de cosméticos para una tienda futura, usar únicamente el texto editorial `COSMÉTICOS PARA LA SIGUIENTE TIENDA`; no inventar titulares ni repetir la lista fuera de la imagen.

Si el tweet tiene mucho texto, listas o varios apartados, NO copiarlo completo en
la imagen. Extraer el dato central y convertirlo en un titular corto (3-7
palabras) más un complemento breve. Mantener nombres importantes solo si son
necesarios para entender la noticia.

- Encuestas/listas: resumir como `NUEVA ENCUESTA DE FORTNITE` y abajo las
  categorías principales (`CELEBRIDADES · PERSONAJES · FRANQUICIAS`), sin meter
  la lista completa.
- Descripciones técnicas largas: elegir el cambio principal y dejar detalles
  secundarios fuera de la imagen.
- Si Isaac pide únicamente título y hashtags, entregar solo esas dos cosas; no
  añadir explicación ni caption largo.

## Collage 2+ imagenes: estilos de acomodo

Estilos disponibles (seleccion **automatica por defecto** segun numero de
imagenes y proporcion; Isaac puede forzar con `--style`):

- **auto** (defecto): cualquier collage de 2 o más imágenes usa `adaptive`,
  incluso cuando todas comparten orientación. Así una horizontal nunca cae en
  una celda vertical por culpa de un layout editorial antiguo.
- **adaptive**: clasifica cada imagen de forma independiente y conserva el
  orden. Ratio ≥1.25 → horizontal 16:9; 0.8–1.25 → cuadrada 1:1; ratio ≤0.8
  → vertical 9:16. Las filas se justifican dentro del área segura para que
  cada celda mantenga su forma sin deformación. Si hay dos imágenes cuadradas
  en un lienzo vertical, las apila una arriba de otra; en lienzos cuadrados o
  apaisados las coloca en fila.
- **grid**: cuadricula uniforme, útil cuando se quiere que todas las celdas
  tengan exactamente la misma forma.
- **bento**: bloques cuadrados/rectangulares de distintos tamanos, ordenado y
  moderno, con hueco de diseno.
- **mosaico**: celdas compactas con separación visual; filas de distinta altura.
- **puzzle**: piezas rectangulares desiguales con separación visual.
- **jerarquico**: una imagen grande protagonista (arriba) + secundarias.
- **asimetrico**: una imagen grande a la izquierda + secundarias a la derecha.

Reglas de recorte: los collages usan **cover centrado** por defecto (nunca
deforman; recortan solo los bordes sobrantes). La tarjeta simple usa
**contain** por defecto. Se puede elegir explícitamente `--fit cover` o
`--fit contain`; este último conserva la imagen completa dentro de su celda.

Si las imágenes tienen tamaños distintos, se avisa pero no se detiene el
proceso: la clasificación usa el ancho/alto real de cada archivo y el motor
las acomoda de manera independiente.

## Video en el tweet

El bot NO edita video: extraer un frame (ffmpeg `-ss <tiempo> -frames:v 1`) y
usarlo como imagen.

## Archivos

- `scripts/compose_image.py`: entrada compatible al compositor canónico de
  `bin/compose_image.py`, 1 o más entradas.
- `bin/fetch_media.py` y `bin/edit_link.py`: descarga ordenada de medios y
  composición desde un enlace.
- `references/presets/fortnite_vertical_image.json`: valores visuales.
