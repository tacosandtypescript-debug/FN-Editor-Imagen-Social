"""Capa de servicio del dashboard.

Aquí vive la lógica del flujo: descubrir, analizar, componer y entregar. No
sabe nada de HTTP, así que se puede probar sola y reutilizar desde otro
frontend si algún día hace falta.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import config
from .pipeline import cards as cards_pipeline
from .pipeline import media as media_pipeline
from .providers import analysis as analysis_providers
from .providers import telegram as telegram_providers
from .providers import timelines as timeline_providers
from .store import Store

#: Máximo de publicaciones nuevas que se enriquecen por sondeo, para no
#: encadenar demasiadas peticiones a los mirrors públicos.
ENRICH_LIMIT = 25


class DashboardError(RuntimeError):
    """Error de negocio con mensaje apto para mostrar en la interfaz."""


class DashboardService:
    """Orquesta el flujo completo sobre el repositorio existente."""

    def __init__(self, store: Store | None = None) -> None:
        config.ensure_directories()
        self.store = store or Store()

    # ------------------------------------------------------------------
    # Estado general
    # ------------------------------------------------------------------
    def state(self) -> dict:
        settings = config.Settings()
        return {
            "app": "EditImg Dashboard",
            "settings": config.redacted(settings),
            "accounts": self.store.list_accounts(),
            "counts": self.store.count_by_status(),
            "timeline_providers": [status.__dict__ for status in timeline_providers.available_providers()],
            "analysis_providers": [status.__dict__ for status in analysis_providers.available_providers()],
            "delivery_providers": [status.__dict__ for status in telegram_providers.available_providers()],
            "profiles": timeline_providers.profile_directories(),
            "events": self.store.recent_events(40),
        }

    def events(self, limit: int = 60) -> list[dict]:
        return self.store.recent_events(limit)

    # ------------------------------------------------------------------
    # Cuentas
    # ------------------------------------------------------------------
    def add_account(self, handle: str) -> dict:
        account = self.store.add_account(handle)
        self.store.log(f"Cuenta añadida: @{account['handle']}")
        return account

    def remove_account(self, handle: str) -> bool:
        removed = self.store.remove_account(handle)
        if removed:
            self.store.log(f"Cuenta eliminada: @{handle}")
        return removed

    def set_account_active(self, handle: str, active: bool) -> dict | None:
        self.store.set_account_active(handle, active)
        return self.store.get_account(handle)

    # ------------------------------------------------------------------
    # Descubrimiento
    # ------------------------------------------------------------------
    def poll(self, handle: str | None = None, provider: str | None = None) -> dict:
        """Busca publicaciones nuevas en las cuentas monitoreadas."""
        accounts = (
            [self.store.get_account(handle)] if handle else self.store.list_accounts(active_only=True)
        )
        accounts = [account for account in accounts if account]
        if not accounts:
            raise DashboardError("no hay cuentas activas que revisar")

        results: list[dict] = []
        total_new = 0
        for account in accounts:
            one = self._poll_account(account, provider)
            total_new += one["new"]
            results.append(one)

        self.store.log(
            f"Sondeo terminado: {total_new} publicación(es) nueva(s) en {len(accounts)} cuenta(s)"
        )
        return {"accounts": results, "new": total_new}

    def _poll_account(self, account: dict, provider: str | None) -> dict:
        handle = account["handle"]
        try:
            outcome = timeline_providers.fetch_timeline(handle, provider)
        except Exception as exc:  # noqa: BLE001 - una cuenta caída no detiene el sondeo
            message = str(exc)[:400]
            self.store.mark_account_checked(handle, error=message)
            self.store.log(f"Fallo al leer @{handle}: {message}", level="error")
            return {"handle": handle, "ok": False, "new": 0, "error": message, "provider": None}

        tweets = outcome["tweets"]
        latest = max((tweet["tweet_id"] for tweet in tweets), default=None)
        # Se enriquecen solo las que aún no están en la base de datos.
        known = {tweet["tweet_id"] for tweet in self.store.list_tweets(limit=1000)}
        fresh = [tweet for tweet in tweets if tweet["tweet_id"] not in known][:ENRICH_LIMIT]
        self._enrich(fresh)

        result = self.store.upsert_tweets(tweets)
        inserted = len(result["inserted"])
        self.store.mark_account_checked(
            handle,
            error=None,
            last_post_id=str(latest) if latest else None,
        )
        note = ""
        if outcome["attempts"]:
            note = " (con respaldo: " + "; ".join(
                f"{a['provider']} falló" for a in outcome["attempts"]
            ) + ")"
        self.store.log(
            f"@{handle}: {inserted} nueva(s), {result['duplicates']} ya conocida(s) "
            f"vía {outcome['provider']}{note}"
        )
        return {
            "handle": handle,
            "ok": True,
            "new": inserted,
            "duplicates": result["duplicates"],
            "total_seen": len(tweets),
            "provider": outcome["provider"],
            "attempts": outcome["attempts"],
        }

    def _enrich(self, tweets: list[dict]) -> None:
        """Completa fecha y medios reales consultando los mirrors públicos.

        El navegador no expone la fecha exacta y Nitter puede recortar medios,
        así que se confirma contra la misma API que ya usa el repositorio.
        """
        for tweet in tweets:
            url = tweet.get("url")
            if not url:
                continue
            try:
                info = media_pipeline.probe_post(url)
            except Exception as exc:  # noqa: BLE001 - el enriquecido es opcional
                tweet["_enrich_error"] = str(exc)[:200]
                continue
            if info.get("posted_at"):
                tweet["posted_at"] = _normalise_date(info["posted_at"]) or tweet.get("posted_at")
            if info.get("post_text"):
                tweet["text"] = info["post_text"]
            if info.get("media_urls"):
                tweet["media"] = info["media_urls"]

    # ------------------------------------------------------------------
    # Publicaciones
    # ------------------------------------------------------------------
    def list_tweets(self, **kwargs) -> list[dict]:
        return self.store.list_tweets(**kwargs)

    def get_tweet(self, tweet_id: str) -> dict:
        tweet = self.store.get_tweet(tweet_id)
        if not tweet:
            raise DashboardError(f"no existe la publicación {tweet_id}")
        return tweet

    def set_tweet_status(self, tweet_id: str, status: str) -> dict:
        self.get_tweet(tweet_id)
        self.store.set_tweet_status(tweet_id, status)
        return self.get_tweet(tweet_id)

    def analyse(self, tweet_id: str, provider: str | None = None) -> dict:
        """Analiza una publicación y guarda la propuesta editorial."""
        tweet = self.get_tweet(tweet_id)
        try:
            result = analysis_providers.analyse_tweet(tweet, provider)
        except Exception as exc:  # noqa: BLE001 - se reporta el motivo tal cual
            message = str(exc)[:400]
            self.store.set_tweet_status(tweet_id, "fallido", detail=message)
            self.store.log(f"Análisis fallido de {tweet_id}: {message}", level="error", tweet_id=tweet_id)
            raise DashboardError(message) from exc

        self.store.update_tweet(tweet_id, analysis_json=result, status="analizado", status_detail=None)
        self.store.log(
            f"Análisis completado ({result['provider']}) para {tweet_id}",
            tweet_id=tweet_id,
        )
        return self.get_tweet(tweet_id)

    # ------------------------------------------------------------------
    # Tarjetas
    # ------------------------------------------------------------------
    def media_directory(self, tweet_id: str) -> Path:
        return config.MEDIA_DIR / str(tweet_id)

    def download_media(self, tweet_id: str) -> dict:
        """Descarga los medios del post una sola vez y los conserva.

        Reutiliza `fetch_media.download_link_info`, así que respeta el orden,
        el límite de 25 MB por archivo y la validación del flujo actual.
        """
        tweet = self.get_tweet(tweet_id)
        target = self.media_directory(tweet_id)
        existing = sorted(
            path for path in target.glob("*") if path.is_file() and not path.name.startswith(".")
        )
        if existing:
            return {"downloaded": False, "images": [str(path) for path in existing]}
        if not tweet.get("media"):
            # Aviso temprano y claro en vez del error genérico del descargador.
            raise DashboardError(
                "la publicación no tiene imágenes y el compositor necesita al menos una"
            )
        if not tweet.get("url"):
            raise DashboardError("la publicación no tiene enlace de origen")
        try:
            info = media_pipeline.download_media(tweet["url"], target)
        except Exception as exc:  # noqa: BLE001
            message = str(exc)[:400]
            self.store.log(f"Fallo al descargar medios de {tweet_id}: {message}", level="error", tweet_id=tweet_id)
            raise DashboardError(f"no se pudieron descargar los medios: {message}") from exc

        images = [item["path"] for item in info["images"]]
        if not images:
            raise DashboardError(
                "la publicación no tiene imágenes: el compositor necesita al menos una"
            )
        # Se guardan las URLs originales en orden (las que realmente se bajaron).
        ordered_urls = [item.get("url") for item in info["images"] if item.get("url")]
        if ordered_urls:
            self.store.update_tweet(tweet_id, media_json=ordered_urls)
        self.store.log(f"Medios descargados para {tweet_id}: {len(images)} archivo(s)", tweet_id=tweet_id)
        return {"downloaded": True, "images": images, "post_text": info.get("post_text")}

    def default_params(self, tweet_id: str) -> dict:
        """Parámetros iniciales de composición a partir del análisis guardado."""
        tweet = self.get_tweet(tweet_id)
        settings = config.Settings()
        analysis = tweet.get("analysis") or {}
        return {
            "top": analysis.get("top") or _fallback_title(tweet),
            "bottom": analysis.get("bottom") or _fallback_context(tweet),
            "format": analysis.get("suggested_format") or "auto",
            "fit": settings.default_fit,
            "style": settings.default_style,
            "resolution": settings.default_resolution,
            "backend": settings.default_backend,
            "caption": analysis.get("caption") or "",
            "hashtags": analysis.get("hashtags") or [],
        }

    def prepare_card(self, tweet_id: str, params: dict | None = None) -> dict:
        """Descarga medios (si hace falta) y compone la tarjeta.

        Cada publicación tiene una sola tarjeta de trabajo: si ya existe, se
        vuelve a componer sobre ella en lugar de acumular copias.
        """
        tweet = self.get_tweet(tweet_id)
        self.download_media(tweet_id)
        images = sorted(
            path for path in self.media_directory(tweet_id).glob("*") if path.is_file()
        )
        merged = {**self.default_params(tweet_id), **(params or {})}
        existing = self.store.latest_card(tweet_id)
        return self._render(
            tweet_id, images, merged, tweet, card_id=existing["id"] if existing else None
        )

    def render_card(self, card_id: int, params: dict | None = None) -> dict:
        """Vuelve a componer una tarjeta existente con nuevos parámetros."""
        card = self.store.get_card(card_id)
        if not card:
            raise DashboardError(f"no existe la tarjeta {card_id}")
        tweet = self.get_tweet(card["tweet_id"])
        images = sorted(
            path for path in self.media_directory(card["tweet_id"]).glob("*") if path.is_file()
        )
        if not images:
            raise DashboardError("no hay medios descargados para esta publicación")
        merged = {**(card.get("params") or {}), **(params or {})}
        return self._render(card["tweet_id"], images, merged, tweet, card_id=card_id)

    def _render(
        self,
        tweet_id: str,
        images: list[Path],
        params: dict,
        tweet: dict,
        card_id: int | None = None,
    ) -> dict:
        # El nombre del archivo lleva la versión para que dos tarjetas de la
        # misma publicación nunca se pisen entre sí.
        if card_id:
            version = int(self.store.get_card(card_id)["version"])
        else:
            previous = self.store.latest_card(tweet_id)
            version = int(previous["version"]) + 1 if previous else 1
        output = config.CARDS_DIR / f"{tweet_id}-v{version}.png"
        try:
            result = cards_pipeline.render_card(images=images, output=output, params=params)
        except cards_pipeline.CardError as exc:
            self.store.set_tweet_status(tweet_id, "fallido", detail=str(exc))
            self.store.log(f"Composición fallida de {tweet_id}: {exc}", level="error", tweet_id=tweet_id)
            raise DashboardError(str(exc)) from exc

        stored_params = {
            **params,
            "top": result["params"]["top"],
            "bottom": result["params"]["bottom"],
            "format": result["params"]["format"],
            "fit": result["params"]["fit"],
            "style": result["params"]["style"],
            "resolution": result["params"]["resolution"],
            "backend": result["params"]["backend"],
        }
        meta = {
            "width": result["width"],
            "height": result["height"],
            "style": result["style"],
            "output_format": result["output_format"],
            "resolution": result["resolution"],
            "images": result["images"],
            "backend": result["backend"],
            "verification": result["verification"],
            "preset": result["preset"],
        }

        if card_id:
            self.store.update_card(card_id, output_path=result["output"], meta=meta, params=stored_params)
            card = self.store.get_card(card_id)
        else:
            card = self.store.create_card(
                tweet_id, stored_params, output_path=result["output"], meta=meta
            )
        self.store.set_tweet_status(tweet_id, "tarjeta_lista")
        self.store.log(
            f"Tarjeta v{card['version']} generada para {tweet_id} "
            f"({result['width']}x{result['height']})",
            tweet_id=tweet_id,
        )
        return card

    def get_card(self, card_id: int) -> dict:
        card = self.store.get_card(card_id)
        if not card:
            raise DashboardError(f"no existe la tarjeta {card_id}")
        return card

    def card_image(self, card_id: int) -> Path:
        card = self.get_card(card_id)
        path = Path(card.get("output_path") or "")
        if not path.is_file():
            raise DashboardError("la tarjeta todavía no tiene imagen generada")
        return path

    # ------------------------------------------------------------------
    # Entrega
    # ------------------------------------------------------------------
    def send_card(
        self,
        card_id: int,
        *,
        caption: str | None = None,
        provider: str | None = None,
    ) -> dict:
        card = self.get_card(card_id)
        tweet = self.get_tweet(card["tweet_id"])
        path = self.card_image(card_id)

        params = card.get("params") or {}
        final_caption = caption
        if final_caption is None:
            final_caption = params.get("caption") or ""
        hashtags = params.get("hashtags") or []
        if hashtags and not any(tag in final_caption for tag in hashtags):
            final_caption = (final_caption + " " + " ".join(hashtags)).strip()
        if not final_caption:
            final_caption = f"{params.get('top', '')} · {tweet.get('url', '')}".strip()

        delivery = telegram_providers.get_delivery_provider(provider)
        status = delivery.status()
        if provider and not status.available:
            raise DashboardError(status.detail)
        try:
            outcome = delivery.send(path, final_caption)
        except Exception as exc:  # noqa: BLE001
            message = str(exc)[:400]
            self.store.record_delivery(card_id, delivery.name, "fallido", message)
            self.store.log(f"Entrega fallida de la tarjeta {card_id}: {message}", level="error")
            raise DashboardError(message) from exc

        self.store.record_delivery(card_id, delivery.name, "ok", json.dumps(outcome, ensure_ascii=False))
        self.store.set_tweet_status(card["tweet_id"], "enviado")
        self.store.log(
            f"Tarjeta {card_id} entregada con {outcome.get('method')} vía {delivery.name}",
            tweet_id=card["tweet_id"],
        )
        return {"delivery": outcome, "caption": final_caption, "card": card}

    def deliveries(self, card_id: int | None = None) -> list[dict]:
        return self.store.list_deliveries(card_id)


# ----------------------------------------------------------------------
def _normalise_date(value: str) -> str | None:
    """Convierte la fecha de los mirrors (`Thu Oct 01 ... +0000 2026`) a ISO."""
    from email.utils import parsedate_to_datetime

    if not value:
        return None
    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return value or None
    return parsed.isoformat()


def _fallback_title(tweet: dict) -> str:
    text = (tweet.get("text") or "").strip()
    first = text.splitlines()[0] if text else ""
    return (first or "TITULAR PENDIENTE")[:90].upper()


def _fallback_context(tweet: dict) -> str:
    posted = str(tweet.get("posted_at") or "")
    if len(posted) >= 10 and posted[4] == "-":
        return f"NOTICIA FORTNITE · {posted[8:10]}/{posted[5:7]}"
    return "NOTICIA FORTNITE"
