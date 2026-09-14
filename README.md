# EditImg — Editor de tarjetas de Fortnite

Proyecto completo para crear tarjetas verticales 1080×1920 y cuadradas
1080×1080 a partir de imágenes o publicaciones de X.

## Incluye

- `bin/compose_image.py`: compositor principal Pillow para 1 o más imágenes.
- `bin/fetch_media.py`: descarga en orden todas las imágenes de un post de X/Twitter.
- `bin/prepare_batch.py`: descarga varios enlaces como un único lote/carrusel,
  conservando el texto, los grupos y el orden de cada fuente.
- `bin/edit_link.py`: une descarga + composición en un solo comando.
- `skills/media/vertical-image-editor/scripts/compose_image.py`: entrada
  compatible con la skill que delega en el compositor principal.
- `skills/media/square-image-editor/`: skill, wrapper y preset para tarjetas 1:1.
- `skills/media/editar-publicar-instagram/`: flujo documentado de entrega al
  publicador externo `igpub`.
- `skills/README.md`: inventario, contrato de integración y verificación de las skills.
- Presets JSON con tipografía, paleta, sombras, márgenes y marca de creador.
- Scripts auxiliares de composición y renderizado de tablas/banderas.
- Skills y reglas de edición en `skills/`.
- Familia tipográfica Barlow incluida en `barlow_font/`.
- Fixtures sintéticos para tests en `tests/fixtures/`.
- Capturas y outputs históricos en `examples/historical/` (no forman parte de los tests).

## Requisitos

- Windows, Linux o macOS para el editor principal
- Linux con X11 si se usa `bin/clipboard_server.py`
- Python 3.10+
- Pillow
- `python-xlib` (solo para `bin/clipboard_server.py`)
- FFmpeg (para extraer fotogramas de vídeos)
- Google Chrome/Chromium (solo para renderizar tablas HTML con banderas)
- Fuente Barlow incluida o instalada en el sistema

Las skills de edición son locales y comparten el compositor canónico. La skill
de publicación en Instagram solo conecta con el proyecto externo `igpub`; no
incluye sus credenciales ni su worker. Consulta `skills/README.md` para el
inventario y la comprobación de esa integración.

Instalación mínima:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 -m pip check
```

### Backend GPU opcional

El compositor puede acelerar redimensionado y fondo desenfocado con CUDA cuando
hay una GPU NVIDIA compatible. La instalación base sigue siendo solo CPU; para
activar el backend GPU instala las dependencias opcionales:

```bash
uv pip install --python .venv/bin/python -r requirements-gpu.txt
.venv/bin/python bin/compose_image.py imagen.jpg salida.png \\
  --top 'TITULAR' --bottom 'CONTEXTO' --backend auto \\
  --preset bin/preset.json
```

`--backend auto` usa CUDA si puede inicializarlo y vuelve a CPU si no; usa
`--backend gpu` para exigir CUDA o `--backend cpu` para desactivarlo. El backend
GPU no cambia el diseño, las dimensiones ni el formato de salida.

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
se pueden forzar explícitamente. `bento`, `mosaico`, `puzzle`, `jerarquico` y
`asimetrico` requieren 2–4 imágenes; otra cantidad produce un error.

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
`W:H`, `W x H` o `W/H`). Por defecto la CLI conserva el tamaño equivalente a
1080 px en el lado corto y se guarda sin compresión PNG. `--resolution 4k`
genera 2160 px en el lado corto: `2160×3840` en vertical y `2160×2160` en
cuadrado, escalando también tipografía, márgenes, sombras y safe areas.

El compositor limita por defecto el lote a 24 imágenes; se puede cambiar con
`--max-images`. Las dimensiones del preset y de la salida se validan antes de
abrir las imágenes para evitar lienzos o entradas excesivamente grandes.

Se puede elegir una imagen distinta para el fondo desenfocado con
`--background fondo.jpg`.

También se puede pasar directamente el enlace de un post de X/Twitter. Se
descargan todas sus imágenes y se componen en el mismo orden:

```bash
python3 bin/edit_link.py 'https://x.com/usuario/status/123456789' salida.png \
  --top 'TITULAR' --bottom 'CONTEXTO' \
  --format 9:16 --preset bin/preset.json
```

El flujo admite además una URL directa de imagen. Por seguridad, limita cada
descarga a 25 MB y el lote a 24 imágenes salvo que se cambie `--max-images`.

Para usar el mismo flujo en una tarjeta cuadrada, usa el preset 1:1. En una
pareja de imágenes, ese preset crea dos paneles cuadrados iguales y los coloca
en una sola fila; `--fit contain` conserva completa cualquier fuente vertical
o de otra proporción dentro de su panel.

```bash
python3 skills/media/square-image-editor/scripts/compose_image.py \
  img1.jpg img2.jpg salida.png \
  --top 'TITULAR {CLAVE|8B3DFF}' \
  --bottom 'CONTEXTO · 03/09' \
  --fit contain \
  --preset skills/media/square-image-editor/references/presets/fortnite_square_image.json
```

Desde un enlace, añade `--format 1:1` y el preset cuadrado al comando de
`bin/edit_link.py`.

`bin/edit_link.py` acepta las mismas opciones de composición (`--background`,
`--style`, `--format`, `--fit`, `--resolution` y `--max-images`) y las valida
con el compositor canónico.

Para varios enlaces enviados juntos, usa `prepare_batch.py` y compón una sola
tarjeta con todos los paths del manifiesto. El titular y la fecha son comunes a
todo el carrusel; usa `--fit contain` para evitar zoom o recortes de capturas.
La respuesta/caption debe llevar exactamente cinco hashtags, incluyendo
`#khetzalgg`; los otros cuatro se eligen consultando las tendencias del día en
[TikTok Creative Center](https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en)
y solo si son relevantes para Fortnite.

La fuente Barlow se resuelve desde `barlow_font/` y el preset funciona aunque el
comando se ejecute desde otro directorio. Las skills para Hermes llevan su
propia fuente, presets y scripts para no depender de esa ruta.

## Hermes Agent

Las skills que Hermes debe cargar están directamente bajo `.hermes/skills/`.
Desde la raíz del repositorio, confía el proyecto una vez y comprueba que el
editor aparezca:

```text
hermes skills trust
hermes skills list
```

Si Hermes ya tenía una sesión abierta, usa `/reset` o inicia una sesión nueva
para reconstruir el índice. El CLI puede mostrar estas skills como `local`; en
el prompt del agente las skills de proyecto se etiquetan como `[project]`.

Las skills de Hermes exportan a 4K por defecto (`2160×3840` vertical o
`2160×2160` cuadrado) y la imagen final debe entregarse como documento/archivo
PNG original; en Telegram equivale a `sendDocument`, nunca `sendPhoto`. Para
volver a la resolución anterior se puede pasar explícitamente
`--resolution native`.

Para instalar solo la skill principal desde este repositorio usa la URL directa
de su `SKILL.md`, no la URL de la carpeta de GitHub:

```text
hermes skills install https://raw.githubusercontent.com/tacosandtypescript-debug/FN-Editor-Imagen-Social/main/.hermes/skills/fortnite-image-editor/SKILL.md
```

El flujo de enlace está diseñado para completar la petición: obtiene el texto
del post, descarga todos los medios, compone, valida con `verify_image.py` y
entrega el PNG. `prepare_link.py` permite separar la descarga y devolver
`post_text` en JSON antes de componer; `edit_link.py` es la variante de una sola
orden cuando el agente ya tiene el texto.

## Pruebas

```bash
python3 -m unittest discover -s tests -v
```

Los tests crean imágenes pequeñas y deterministas en un directorio temporal;
los archivos de `examples/historical/` son únicamente material de trabajos
anteriores y no se usan como fixtures.

El workflow de GitHub Actions repite la compilación y la suite en Python 3.10,
3.11, 3.12 y 3.13. Las dependencias de ejecución están fijadas en
`requirements.txt`.

## Reglas visuales

- Lienzo vertical 1080×1920 o cuadrado 1080×1080 según el preset.
- Márgenes seguros y texto centrado.
- Barlow Black Italic.
- Paleta Halloween: morado, naranja, magenta y dorado.
- Marca fija: `CÓDIGO: KHETZALGG`.
- Un color por palabra; en el estilo habitual: una palabra arriba y dos abajo.
- El texto normal es blanco y no se colorean palabras funcionales (`de`, `la`,
  `los`, `a`, `o`, `y`, `que`, `se`); el compositor rechaza esos marcadores.
- El titular y el contexto siempre son obligatorios; el compositor rechaza
  textos vacíos para impedir tarjetas sin texto.
- Las tablas de banderas/horarios se tratan como un bloque único centrado.
- En modo `adaptive`, horizontal significa ratio ≥ 1.25, cuadrada significa
  0.8–1.25 y vertical significa ratio ≤ 0.8.

## Telegram / publicación

La entrega de imágenes debe hacerse como documento PNG original, nunca como foto comprimida. La publicación a Instagram usa el sistema externo `igpub`; sus credenciales y configuración no se incluyen en este repositorio.

## Seguridad

No se incluyen tokens, contraseñas, archivos de sesión ni entornos virtuales. Configura cualquier credencial mediante variables de entorno o archivos locales ignorados por Git.
