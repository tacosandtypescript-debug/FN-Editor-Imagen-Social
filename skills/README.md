# Skills integradas

Este proyecto mantiene cuatro skills relacionadas con el flujo de edición y
publicación de imágenes. Para Hermes, la fuente autoritativa está en
`.hermes/skills/`: cada skill tiene su `SKILL.md` directamente dentro de la
carpeta y lleva consigo scripts, presets y fuente. Esa forma evita que Hermes
ignore la skill por estar bajo `skills/media/` o que un wrapper falle al salir
de la raíz del repositorio.

Las copias de `skills/media/` son compatibilidad histórica para el proyecto
original y no son las que debe elegir Hermes cuando encuentre las skills de
`.hermes/`.

| Skill | Versión | Entrada principal | Estado |
| --- | --- | --- | --- |
| `fortnite-image-editor` | 3.3.0 | `scripts/edit_link.py` | Dispatcher principal: decide automáticamente vertical, cuadrada u horizontal y exige descargar, componer, validar y entregar. |
| `vertical-image-editor` | 3.3.0 | `scripts/edit_link.py` | Skill autocontenida para 9:16 / 1080×1920, compatible con Windows. |
| `square-image-editor` | 3.3.0 | `scripts/edit_link.py` | Skill autocontenida para 1:1 / 1080×1080, compatible con Windows. |
| `editar-publicar-instagram` | 2.0.0 | `SKILL.md` | Flujo documentado hacia el proyecto externo `igpub`; Linux-only, sin credenciales ni publicador local. |

## Contrato de integración

- Las skills nuevas de Hermes contienen una copia autocontenida de
  `compose_image.py`; así una instalación desde la URL de `SKILL.md` conserva
  todos sus archivos de soporte.
- `prepare_link.py` descarga el carrusel, extrae el texto del post cuando la
  API pública lo ofrece y devuelve un manifiesto JSON. `edit_link.py` resuelve
  descarga + composición en una orden.
- Las copias históricas de `skills/media/` siguen delegando en
  `bin/compose_image.py` para conservar compatibilidad con el repositorio
  anterior.
- `bin/edit_link.py` comparte los mismos estilos, límites y opciones del
  compositor canónico.
- La skill de Instagram no intenta publicar por sí sola: requiere definir
  `IGPUB_DIR` y validar que el proyecto externo tenga `enqueue.py`,
  `queue_worker.py` y `queue/in/`.

## Dependencias

La edición local requiere Python 3.10+, Pillow y la fuente Barlow. Las skills
de Hermes incluyen su propia copia; la CLI histórica usa `barlow_font/`.
`python-xlib` solo es necesario para
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

La suite comprueba los metadatos y archivos de las skills históricas y de los
paquetes autocontenidos de Hermes, la carga de los presets, la ejecución de los
compositores y la integración de las opciones del compositor. La publicación
real en Instagram no se prueba aquí porque depende de la instalación externa
de `igpub` y de sus credenciales locales.
