"""Interfaces comunes de los proveedores.

Cada capacidad del flujo (descubrir publicaciones, analizarlas y entregarlas)
vive detrás de una interfaz propia. Así se puede cambiar una pieza sin tocar
las demás, que es justo lo que se pidió: módulos separados y mantenibles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class ProviderStatus:
    """Estado de disponibilidad de un proveedor, para mostrarlo en la interfaz."""

    name: str
    available: bool
    detail: str = ""


@dataclass
class TweetRecord:
    """Publicación normalizada, independiente de la fuente que la obtuvo."""

    tweet_id: str
    source_handle: str
    text: str | None = None
    url: str | None = None
    author_handle: str | None = None
    posted_at: str | None = None
    relative_time: str | None = None
    media: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "tweet_id": self.tweet_id,
            "source_handle": self.source_handle,
            "text": self.text,
            "url": self.url,
            "author_handle": self.author_handle or self.source_handle,
            "posted_at": self.posted_at,
            "relative_time": self.relative_time,
            "media": list(self.media),
        }


@dataclass
class Analysis:
    """Resultado del análisis editorial de una publicación."""

    top: str
    bottom: str
    caption: str = ""
    hashtags: list[str] = field(default_factory=list)
    suggested_format: str | None = None
    suggested_style: str | None = None
    reasoning: str | None = None
    provider: str = ""
    raw: str | None = None

    def as_dict(self) -> dict:
        return {
            "top": self.top,
            "bottom": self.bottom,
            "caption": self.caption,
            "hashtags": list(self.hashtags),
            "suggested_format": self.suggested_format,
            "suggested_style": self.suggested_style,
            "reasoning": self.reasoning,
            "provider": self.provider,
        }


class ProviderError(RuntimeError):
    """Fallo controlado de un proveedor, con mensaje para el usuario."""


@runtime_checkable
class TimelineProvider(Protocol):
    """Obtiene las publicaciones recientes de una cuenta."""

    name: str

    def status(self) -> ProviderStatus: ...

    def fetch(self, handle: str) -> list[dict]: ...


@runtime_checkable
class AnalysisProvider(Protocol):
    """Analiza una publicación y propone texto y estructura de tarjeta."""

    name: str

    def status(self) -> ProviderStatus: ...

    def analyse(self, tweet: dict) -> Analysis: ...


@runtime_checkable
class DeliveryProvider(Protocol):
    """Entrega una tarjeta terminada."""

    name: str

    def status(self) -> ProviderStatus: ...

    def send(self, image_path, caption: str = "") -> dict: ...
