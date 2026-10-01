"""Entrega de tarjetas por Telegram.

El repositorio no incluía ningún emisor: solo la regla documentada de
entregar el PNG original con `sendDocument`, nunca como foto comprimida
(equivalente a `sendPhoto`). Este módulo implementa esa regla con la
biblioteca estándar y la Bot API oficial.

No se guardan credenciales en el repositorio: el token y el chat se leen de
`dashboard/.env` o de variables de entorno.
"""

from __future__ import annotations

import json
import mimetypes
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .. import config
from .base import DeliveryProvider, ProviderError, ProviderStatus

API_ROOT = "https://api.telegram.org"
#: Los documentos se envían tal cual; 50 MB es el límite de la Bot API.
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
#: Telegram recorta los captions de documento a 1024 caracteres.
MAX_CAPTION_CHARS = 1024


class TelegramDelivery(DeliveryProvider):
    """Envía el PNG como documento con `sendDocument`."""

    name = "telegram"

    def __init__(self, token: str | None = None, chat_id: str | None = None) -> None:
        settings = config.Settings()
        self.token = (token if token is not None else settings.telegram_bot_token).strip()
        self.chat_id = str(chat_id if chat_id is not None else settings.telegram_chat_id).strip()

    def status(self) -> ProviderStatus:
        if not self.token or not self.chat_id:
            return ProviderStatus(
                self.name,
                False,
                "falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en dashboard/.env",
            )
        return ProviderStatus(self.name, True, f"chat configurado: {self.chat_id}")

    def send(self, image_path, caption: str = "") -> dict:
        path = Path(image_path)
        if not path.is_file():
            raise ProviderError(f"no existe la tarjeta: {path}")
        size = path.stat().st_size
        if size > MAX_DOCUMENT_BYTES:
            raise ProviderError(
                f"la tarjeta pesa {size // (1024 * 1024)} MB y Telegram admite 50 MB"
            )
        if not self.token or not self.chat_id:
            raise ProviderError(
                "Telegram no está configurado: añade TELEGRAM_BOT_TOKEN y "
                "TELEGRAM_CHAT_ID en dashboard/.env"
            )

        url = f"{API_ROOT}/bot{self.token}/sendDocument"
        fields = {"chat_id": self.chat_id}
        if caption:
            fields["caption"] = caption[:MAX_CAPTION_CHARS]
        body, content_type = _multipart(fields, "document", path)
        request = Request(url, data=body, method="POST")
        request.add_header("Content-Type", content_type)

        try:
            with urlopen(request, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise ProviderError(_describe_http_error(exc)) from exc
        except URLError as exc:
            raise ProviderError(f"no se pudo contactar con Telegram: {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise ProviderError(f"fallo de red al enviar a Telegram: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("Telegram devolvió una respuesta ilegible") from exc

        if not payload.get("ok"):
            raise ProviderError(
                "Telegram rechazó el envío: "
                + str(payload.get("description") or "sin detalle")
            )
        result = payload.get("result") or {}
        return {
            "ok": True,
            "provider": self.name,
            "method": "sendDocument",
            "message_id": result.get("message_id"),
            "chat_id": (result.get("chat") or {}).get("id", self.chat_id),
            "document_id": (result.get("document") or {}).get("file_id"),
            "file_size": size,
        }


class LocalDelivery(DeliveryProvider):
    """No envía nada: deja la tarjeta lista en disco para enviarla a mano."""

    name = "local"

    def status(self) -> ProviderStatus:
        return ProviderStatus(self.name, True, "la tarjeta se guarda en dashboard/var/cards")

    def send(self, image_path, caption: str = "") -> dict:
        path = Path(image_path)
        if not path.is_file():
            raise ProviderError(f"no existe la tarjeta: {path}")
        return {
            "ok": True,
            "provider": self.name,
            "method": "archivo-local",
            "path": str(path),
            "file_size": path.stat().st_size,
            "caption": caption,
        }


def _multipart(fields: dict[str, str], file_field: str, path: Path):
    """Construye un cuerpo multipart/form-data sin dependencias externas."""
    boundary = f"----EditImgDashboard{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")

    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{path.name}"\r\n'
        ).encode("utf-8")
    )
    chunks.append(f"Content-Type: {mime}\r\n\r\n".encode("utf-8"))
    chunks.append(path.read_bytes())
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _describe_http_error(exc: HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        description = payload.get("description")
    except Exception:  # noqa: BLE001 - cualquier fallo se traduce a un mensaje genérico
        description = None
    base = f"Telegram respondió {exc.code}"
    if description:
        base += f": {description}"
    if exc.code == 401:
        base += " (revisa el token del bot)"
    elif exc.code == 400 and description and "chat not found" in description.lower():
        base += " (revisa el chat_id y que hayas iniciado el bot)"
    elif exc.code == 403:
        base += " (el bot no puede escribir en ese chat)"
    return base


def get_delivery_provider(name: str | None = None) -> DeliveryProvider:
    """Devuelve el proveedor de entrega pedido, con respaldo local."""
    chosen = (name or "").strip().lower()
    if chosen in {"", "auto", "telegram"}:
        telegram = TelegramDelivery()
        if telegram.status().available:
            return telegram
        if chosen == "telegram":
            return telegram
        return LocalDelivery()
    if chosen == "local":
        return LocalDelivery()
    raise ProviderError(f"proveedor de entrega desconocido: {chosen}")


def available_providers() -> list[ProviderStatus]:
    return [TelegramDelivery().status(), LocalDelivery().status()]
