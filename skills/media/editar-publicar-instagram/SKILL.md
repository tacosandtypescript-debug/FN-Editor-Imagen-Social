---
name: editar-publicar-instagram
description: "Usar cuando Isaac pida publicar en Instagram o mandar a @StoryinstaTiktokbot."
version: 1.1.0
author: Isaac
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [instagram, publicar, storybot, igpub, post, historias, flujo, imagenes]
---

# Publicar en Instagram — flujo de imágenes (perfil @Editimagenbot)

Este perfil edita **solo imágenes** (tarjetas verticales 1080×1920 de noticias
Fortnite) y entrega el PNG/JPG final al publicador de IG (@StoryinstaTiktokbot,
cuenta @khetzalgg) **sin mensajería Telegram bot-a-bot**: todos los procesos
viven en la misma máquina y la **cola igpub** es el canal de entrega. Los
vídeos NO se editan aquí: eso lo hace @hermeserositobot.

## Regla de oro: el canal es la cola, no Telegram

Un bot NO puede mandarse DM con otro bot por Telegram. Cuando Isaac diga
"súbelo a IG", "mándale a StoryinstaTiktokbot", "publícalo en historias/post"...
con una imagen terminada, **no** hay que intentar escribirle al otro bot: el
archivo se entrega **encolándolo en igpub**. El worker del publicador lo sube
solo a IG en orden FIFO.

```
imagen editada (esta máquina)  ->  python3 ~/igpub/enqueue.py post|story <imagen> [--caption]
                                     ->  queue/in  ->  queue_worker.py  ->  Instagram @khetzalgg
```

## Pasos

1. **Editar la imagen** con la skill `vertical-image-editor`
   (`compose_image.py` con preset `fortnite_vertical_image`, reglas de estilo
   aprobadas: márgenes seguros, sombra, marca de agua KHETZALGG, titular
   arriba + contexto/fecha abajo). Entregar el resultado con MEDIA antes de
   preguntar por publicación.
2. **Decidir el tipo IG** según lo que pida Isaac:
   - publicación en el feed / carrusel → `post` (imagen; con caption)
   - historia → `story` (imagen 9:16; **sin caption**)
3. **Encolar** (nunca publicar en primer plano con `ig_publish.py`):
   ```bash
   python3 /home/isaac/igpub/enqueue.py <post|story> <imagen.png> [--caption "texto"]
   ```
4. Responder breve: `✅ en cola — sube solo` (con tipo).

## Verificación

```bash
# worker vivo (pid del proceso publicador)
ps -p $(cat /home/isaac/igpub/queue/worker.pid)

# estado de publicaciones
tail /home/isaac/igpub/queue/worker.log      # OK <id> media_id=...
ls /home/isaac/igpub/queue/done              # exitosos
ls /home/isaac/igpub/queue/failed            # fallidos (avisar a Isaac cuál reenviar)
```

Si el worker está muerto (sin pid / sin proceso), relanzarlo en background:
`cd /home/isaac/igpub && python3 queue_worker.py` (proceso continuo, no
bloqueante). Tras reiniciar el gateway de storybot el worker puede caerse:
comprobar siempre antes de encolar.

## Reglas de texto

- **Post**: caption = descripción + 4-6 hashtags (obligatorio #khetzalgg en
  contenido Fortnite). El título/hashtags que ya acompañan a la imagen en
  Telegram son la base del caption.
- **Historia**: sin caption (IG no lo muestra); el texto va DENTRO de la
  imagen editada (regla de vertical-image-editor: titular arriba, contexto
  abajo).
- Nunca encolar como `reel`: eso es para vídeo (@hermeserositobot).

## Particularidades de este perfil

- Isaac edita aquí imágenes de noticias (`vertical-image-editor` para la
  composición; una tarjeta por tweet, o carrusel si son varias).
- Si Isaac adjunta un VÍDEO pidiendo editar/publicar, indicarle que eso lo
  hace @hermeserositobot (este perfil es solo imágenes). Si el tweet trae
  vídeo pero Isaac quiere tarjeta, sacar un frame y editar la imagen.

## Pitfalls

- NO ejecutar `ig_publish.py` en primer plano: el siguiente mensaje de Isaac
  interrumpe la publicación a mitad.
- Encolar SOLO la imagen final validada de la petición actual; no re-encolar
  trabajos viejos ni adivinar archivos de mensajes anteriores.
- El worker publica uno a uno en orden: si hay cola acumulada, avisar cuántos
  quedan por delante.
