---
name: vertical-image-editor
description: "Use when editing Fortnite news images into vertical cards."
version: 2.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [imagenes, fortnite, vertical, 9:16, blur, collage, telegram]
---

# Vertical Image Editor — tarjetas verticales de noticias Fortnite

Crea tarjetas verticales 1080x1920 (9:16) con fondo blur, imagen(es) sin
deformar, sombra difusa de 2 capas y texto con palabras de color.

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
2. Bajar las imagenes con `?name=orig` (o capturar frame si es video: ffmpeg).
3. Editar en espanol con titular inventado por el bot si Isaac no indica texto.
   NO esperar aprobacion cuando Isaac esta en modo "practica/entrenamiento":
   generar directo en estilo Directo (arriba hecho, abajo fecha/contexto).
4. Entregar las tarjetas con MEDIA y un titulo + hashtags (1 caption por
   carrusel si son varias).

## Comando

```bash
python3 scripts/compose_image.py img1.jpg [img2..img4] salida.jpg \
  --top 'TEXTO {CLAVE|FFD700}' --bottom 'CONTEXTO · 03/09' \
  [--style auto|grid|bento|mosaico|puzzle|jerarquico|asimetrico] \
  --preset references/presets/fortnite_vertical_image.json
```

El ULTIMO argumento es la salida. 1 imagen = tarjeta simple; 2-4 = collage.

## Reglas de estilo (aprobadas por Isaac)

- **Margenes seguros SIEMPRE**: 90 px arriba/abajo y 60 px a cada lado
  (texto e imagenes dentro de la zona segura; nada pegado a bordes).
- **Sombra de imagen**: UNA sola sombra exterior amplia, visible y difusa (blur
  86, opacidad 68), con máscara expandida antes del blur para que no quede
  cuadrada.
- **Marca de agua**: añadir siempre `CÓDIGO: KHETZALGG` centrado en la zona
  inferior, separado del borde (145 px desde abajo), en blanco semitransparente
  (opacidad 125, tamaño 46). No pegarlo al límite ni ponerlo sobre el titular.
- **Sombra de texto**: una sombra negra independiente, pequeña y difuminada
  (blur 14, desplazamiento 3x5, opacidad 72), sin doble contorno.
- **Sin creditos**: nunca poner "VIA @cuenta" ni el autor dentro de la imagen.
- **Tamaño uniforme**: todas las líneas del titular y del contexto deben usar el mismo tamaño de tipografía. Si una línea no cabe, dividir la frase en líneas más cortas antes de reducir el tamaño. Después, comprobar que el grupo completo queda dentro de los 90 px de margen superior e inferior.
  (outline) 6 px. Si un titular no cabe en una línea con letra grande, partirlo
  en 2 líneas separadas por `\n` en `--top` (cada línea conserva su color y se
  centra).
- **Colores sin repetir**: dentro de una misma tarjeta, no repetir un color en dos palabras. Distribuir los colores de la paleta Halloween entre las palabras destacadas; si arriba se usan morado y naranja, abajo usar magenta y dorado.
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

## Collage 2-4 imagenes: estilos de acomodo

Estilos disponibles (seleccion **automatica por defecto** segun numero de
imagenes y proporcion; Isaac puede forzar con `--style`):

- **auto** (defecto): elige el mejor segun N y la proporcion de la 1ª imagen
  (N<=2 -> grid; N=3 -> jerarquico si son anchas, asimetrico si verticales,
  mosaico si cuadradas; N=4 -> jerarquico si anchas, asimetrico si verticales,
  bento si cuadradas/cercanas).
- **grid**: cuadricula uniforme (legacy, contain).
- **bento**: bloques cuadrados/rectangulares de distintos tamanos, ordenado y
  moderno, con hueco de diseno.
- **mosaico**: celdas que encajan sin espacios; filas de distinta altura.
- **puzzle**: piezas rectangulares desiguales que encajan sin huecos.
- **jerarquico**: una imagen grande protagonista (arriba) + secundarias.
- **asimetrico**: una imagen grande a la izquierda + secundarias a la derecha.

Reglas de los estilos nuevos: rellenan su celda con **cover centrado** (nunca
deforman; recortan solo los bordes sobrantes). La tarjeta simple (1 imagen) y
`--style grid` conservan **contain** (imagen completa, sin recorte).

Disposicion automatica LEGACY (solo `grid`) segun r = ancho/alto y N:

- r >= 1.2 (anchas/panoramicas): COLUMNA apiladas.
- r <= 0.8 (verticales): FILA en linea horizontal.
- 0.8 < r < 1.2 (cuadradas/cercanas): N=2 columna, N=3 fila, N=4 rejilla 2x2.
- Si las imagenes NO son del mismo tamano, avisar y preguntar; por defecto
  celdas uniformes.

## Video en el tweet

El bot NO edita video: extraer un frame (ffmpeg `-ss <tiempo> -frames:v 1`) y
usarlo como imagen.

## Archivos

- `scripts/compose_image.py`: composicion (Pillow), 1-4 entradas.
- `references/presets/fortnite_vertical_image.json`: valores visuales.
