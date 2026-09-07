# EditImg — Editor de tarjetas verticales de Fortnite

Proyecto completo para crear tarjetas 1080×1920 a partir de imágenes o publicaciones de X.

## Incluye

- `bin/compose_image.py`: compositor principal Pillow para 1–4 imágenes.
- Presets JSON con tipografía, paleta, sombras, márgenes y marca de creador.
- Scripts auxiliares de composición y renderizado de tablas/banderas.
- Skills y reglas de edición en `skills/`.
- Familia tipográfica Barlow incluida en `barlow_font/`.
- Material de trabajo y ejemplos en `jobs/`.

## Requisitos

- Linux
- Python 3.10+
- Pillow
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
python3 bin/compose_image.py imagen.jpg salida.png \\
  --top 'TITULAR {PALABRA|8B3DFF}' \\
  --bottom 'CONTEXTO DE LA NOTICIA' \\
  --preset bin/preset.json
```

La salida PNG conserva 1080×1920 y se guarda sin compresión PNG. Las imágenes no se deforman; se adaptan con fondo blur y sujeto contenido.

## Reglas visuales

- Lienzo vertical 1080×1920.
- Márgenes seguros y texto centrado.
- Barlow Black Italic.
- Paleta Halloween: morado, naranja, magenta y dorado.
- Marca fija: `CÓDIGO: KHETZALGG`.
- Un color por palabra; en el estilo habitual: una palabra arriba y dos abajo.
- Las tablas de banderas/horarios se tratan como un bloque único centrado.

## Telegram / publicación

La entrega de imágenes debe hacerse como documento PNG original, nunca como foto comprimida. La publicación a Instagram usa el sistema externo `igpub`; sus credenciales y configuración no se incluyen en este repositorio.

## Seguridad

No se incluyen tokens, contraseñas, archivos de sesión ni entornos virtuales. Configura cualquier credencial mediante variables de entorno o archivos locales ignorados por Git.
