# Plantilla de horarios verticales

Plantilla reutilizable para una segunda tarjeta de horarios de Fortnite.

## Características

- Salida vertical PNG RGBA de `2160×3840`.
- Usa únicamente el fondo difuminado; no conserva una imagen nítida detrás del panel.
- Las banderas se renderizan grandes, separadas y en una columna independiente de las horas.
- La fila con siete banderas se divide en dos líneas para evitar superposiciones.
- Usa `NotoColorEmoji.ttf` del repositorio `Keinta15/Magisk-iOS-Emoji`.
- Usa la tipografía de proyecto `Barlow Black Italic` y conserva `CÓDIGO: KHETZALGG`.

## Repetirla

Desde la raíz del repositorio:

```bash
python3 templates/horarios-v42-20-vertical/render_schedule.py \\
  --source /ruta/a/la/imagen.jpg \\
  --output /ruta/a/horarios.png \\
  --schedule templates/horarios-v42-20-vertical/schedule.json \\
  --version V42.20 \\
  --date 16/09/2026
```

Edita `schedule.json` para cambiar banderas u horas. La fuente se utiliza solo
para construir el fondo difuminado.

## Recurso de emojis

`assets/NotoColorEmoji.ttf` se copió desde:

- Repositorio: https://github.com/Keinta15/Magisk-iOS-Emoji
- Commit de origen: `ecac477a849d`
- Licencia: MIT, conservada en `assets/NotoColorEmoji.LICENSE`.
