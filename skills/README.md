# Skills integradas

Este proyecto mantiene tres skills relacionadas con el flujo de edición y
publicación de imágenes:

| Skill | Versión | Entrada principal | Estado |
| --- | --- | --- | --- |
| `vertical-image-editor` | 2.2.0 | `scripts/compose_image.py` | Integrada localmente; usa el compositor canónico y el preset vertical. |
| `square-image-editor` | 1.1.0 | `scripts/compose_image.py` | Integrada localmente; usa el compositor canónico y el preset cuadrado. |
| `editar-publicar-instagram` | 1.2.0 | `SKILL.md` | Flujo documentado hacia el proyecto externo `igpub`; no contiene credenciales ni un publicador local. |

## Contrato de integración

- La única implementación del compositor es `bin/compose_image.py`.
- Los wrappers de las dos skills de edición delegan en ese archivo para evitar
  divergencias entre la CLI y las skills.
- Cada skill de edición contiene su `SKILL.md`, wrapper Python y preset JSON
  versionado.
- `bin/edit_link.py` comparte los mismos estilos, límites y opciones del
  compositor canónico.
- La skill de Instagram no intenta publicar por sí sola: requiere definir
  `IGPUB_DIR` y validar que el proyecto externo tenga `enqueue.py`,
  `queue_worker.py` y `queue/in/`.

## Dependencias

La edición local requiere Python 3.10+, Pillow y la fuente Barlow incluida en
`barlow_font/`. `python-xlib` solo es necesario para
`bin/clipboard_server.py`. FFmpeg y Google Chrome/Chromium son herramientas
opcionales para capturar frames de vídeo o renderizar tablas HTML.

## Verificación

Desde la raíz del repositorio:

```bash
python3 -m pip install -r requirements.txt
python3 -m pip check
python3 -m compileall -q bin skills tests
python3 -m unittest discover -s tests -v
```

La suite comprueba los metadatos y archivos de las skills, la carga de los
presets, la ejecución de ambos wrappers y la integración de las opciones del
compositor. La publicación real en Instagram no se prueba aquí porque depende
de la instalación externa de `igpub` y de sus credenciales locales.
