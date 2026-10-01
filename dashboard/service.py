"""Capa de servicio del dashboard.

Aquí vive la lógica del flujo: descubrir, analizar, componer y entregar. No
sabe nada de HTTP, así que se puede probar sola y reutilizar desde otro
frontend si algún día hace falta.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import config
from . import timefmt
from .clock import CLOCK
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
        self.sync_clock()
        return {
            "app": "EditImg Dashboard",
            "settings": config.redacted(settings),
            "accounts": self.store.list_accounts(),
            "counts": self.store.count_by_status(),
            "timeline_providers": [status.__dict__ for status in timeline_providers.available_providers()],
            "analysis_providers": [status.__dict__ for status in analysis_providers.available_providers()],
            "delivery_providers": [status.__dict__ for status in telegram_providers.available_providers()],
            "profiles": timeline_providers.profile_directories(),
            "clock": self.clock_status(),
            "events": self.store.recent_events(40),
        }

    # ------------------------------------------------------------------
    # Reloj
    # ------------------------------------------------------------------
    def sync_clock(self, force: bool = False) -> dict:
        """Asegura que la hora real está medida y la guarda para el próximo arranque.

        Es importante por dos motivos independientes: el reloj del sistema
        puede estar desviado, y la zona configurada puede no ser la del
        usuario. Sin corregir lo primero, «hace X minutos» se calcula contra
        una hora falsa; sin lo segundo, la fecha mostrada sería de otro país.
        """
        settings = config.Settings()
        CLOCK.timezone_name = settings.timezone_name
        if settings.utc_offset_hours is not None:
            CLOCK.set_local_offset_override(settings.utc_offset_hours)

        first_measurement = CLOCK._measured_at is None  # noqa: SLF001 - arranque
        if not force and not first_measurement and not CLOCK.is_stale():
            return CLOCK.status()

        # Se parte del valor guardado para tener hora correcta aunque la red
        # falle, pero se vuelve a medir: el reloj puede haberse movido desde el
        # arranque anterior, como de hecho ocurre en esta máquina.
        stored = self.store.get_setting("clock_offset_seconds")
        if stored is not None and first_measurement:
            try:
                CLOCK.set_offset(float(stored), source="guardado", trusted=False)
            except (TypeError, ValueError):
                pass

        # `measure()` conserva el valor anterior si no logra ninguna muestra.
        status = CLOCK.measure()
        if status.get("trusted"):
            self.store.set_setting("clock_offset_seconds", str(status["offset_seconds"]))
            self.store.set_setting("clock_source", str(status.get("source") or ""))
        return status

    def clock_status(self) -> dict:
        status = CLOCK.status()
        status["now_utc"] = CLOCK.now().replace(microsecond=0).isoformat()
        status["now_local"] = (
            CLOCK.to_local(CLOCK.now()).replace(tzinfo=None, microsecond=0).isoformat()
        )
        return status

    def now(self):
        return CLOCK.now()

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
    def poll(
        self,
        handle: str | None = None,
        provider: str | None = None,
        progress: dict | None = None,
    ) -> dict:
        """Busca publicaciones nuevas en las cuentas monitoreadas.

        Se leen todas las cuentas con una sola pasada del proveedor elegido:
        con el navegador eso significa **una única ventana de Chrome** para
        todas, en lugar de abrir y cerrar una por cuenta.
        """
        if handle:
            account = self.store.get_account(handle)
            accounts = [account] if account else []
        else:
            accounts = self.store.list_accounts(active_only=True)
        if not accounts:
            raise DashboardError("no hay cuentas activas que revisar")

        handles = [account["handle"] for account in accounts]
        if progress is not None:
            progress.update({"total": len(handles), "done": 0, "current": None, "running": True})

        try:
            batch = timeline_providers.fetch_timelines_batch(handles, provider)
        except Exception as exc:  # noqa: BLE001 - se informa cuenta por cuenta
            message = str(exc)[:400]
            for account in accounts:
                self.store.mark_account_checked(account["handle"], error=message)
            self.store.log(f"Sondeo fallido: {message}", level="error")
            if progress is not None:
                progress.update({"done": len(handles), "current": None, "running": False})
            return {
                "accounts": [
                    {"handle": name, "ok": False, "new": 0, "error": message}
                    for name in handles
                ],
                "new": 0,
                "provider": None,
            }

        provider_name = batch["provider"]
        results: list[dict] = []
        total_new = 0
        for account in accounts:
            name = account["handle"]
            if progress is not None:
                progress["current"] = name
            outcome = batch["results"].get(name)
            if isinstance(outcome, Exception):
                one = self._record_failure(name, str(outcome))
            else:
                one = self._record_success(name, outcome, provider_name, batch.get("attempts"))
            total_new += one["new"]
            results.append(one)
            if progress is not None:
                progress["done"] += 1

        if progress is not None:
            progress.update({"current": None, "running": False})
        self.store.log(
            f"Sondeo terminado: {total_new} publicación(es) nueva(s) en {len(accounts)} cuenta(s) "
            f"vía {provider_name}"
        )
        return {"accounts": results, "new": total_new, "provider": provider_name}

    def _record_success(
        self,
        handle: str,
        tweets: list[dict],
        provider_name: str | None,
        attempts: list[dict] | None = None,
    ) -> dict:
        latest = max((tweet["tweet_id"] for tweet in tweets), default=None)
        # Se enriquecen solo las que aún no están en la base de datos.
        known = {tweet["tweet_id"] for tweet in self.store.list_tweets(limit=1000)}
        fresh = [tweet for tweet in tweets if tweet["tweet_id"] not in known][:ENRICH_LIMIT]
        self._enrich(fresh)

        result = self.store.upsert_tweets(tweets)
        inserted = len(result["inserted"])
        self.store.mark_account_checked(
            handle, error=None, last_post_id=str(latest) if latest else None
        )
        note = ""
        if attempts:
            note = " (con respaldo: " + "; ".join(
                f"{item['provider']} falló" for item in attempts
            ) + ")"
        self.store.log(
            f"@{handle}: {inserted} nueva(s), {result['duplicates']} ya conocida(s) "
            f"vía {provider_name}{note}"
        )
        return {
            "handle": handle,
            "ok": True,
            "new": inserted,
            "duplicates": result["duplicates"],
            "total_seen": len(tweets),
            "provider": provider_name,
            "attempts": attempts or [],
        }

    def _record_failure(self, handle: str, message: str) -> dict:
        detail = str(message)[:400]
        self.store.mark_account_checked(handle, error=detail)
        self.store.log(f"Fallo al leer @{handle}: {detail}", level="error")
        return {"handle": handle, "ok": False, "new": 0, "error": detail, "provider": None}

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
        cards = self.store.latest_card_ids()
        tweets = [
            timefmt.decorate_tweet(tweet, CLOCK) for tweet in self.store.list_tweets(**kwargs)
        ]
        for tweet in tweets:
            card_id = cards.get(tweet["tweet_id"])
            tweet["card_id"] = card_id
            tweet["has_card"] = card_id is not None
            if card_id:
                tweet["editor_url"] = f"/editor.html?card={card_id}"
        return tweets

    def get_tweet(self, tweet_id: str) -> dict:
        tweet = self.store.get_tweet(tweet_id)
        if not tweet:
            raise DashboardError(f"no existe la publicación {tweet_id}")
        return timefmt.decorate_tweet(tweet, CLOCK)

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

    def process_tweet(
        self,
        tweet_id: str,
        params: dict | None = None,
        provider: str | None = None,
    ) -> dict:
        """Prepara una publicación de principio a fin, en un solo paso.

        Es lo que dispara el botón «Procesar»: analiza si todavía no hay
        análisis, descarga los medios y compone la tarjeta. Deja el resultado
        listo para abrir en el editor independiente.
        """
        tweet = self.get_tweet(tweet_id)
        if not tweet.get("media"):
            raise DashboardError(
                "la publicación no tiene imágenes: el compositor necesita al menos una"
            )
        done: list[str] = []
        if not tweet.get("analysis"):
            self.analyse(tweet_id, provider)
            done.append("análisis de texto")

        card = self.prepare_card(tweet_id, params)
        done.append("descarga de medios y composición")

        return {
            "card": card,
            "tweet": self.get_tweet(tweet_id),
            "steps": done,
            "editor_url": f"/editor.html?card={card['id']}",
        }

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

    def regenerate_text(
        self,
        card_id: int,
        *,
        instructions: str | None = None,
        render: bool = True,
        provider: str | None = None,
    ) -> dict:
        """Vuelve a generar **solo el texto**: titular, texto inferior y caption.

        No toca los medios descargados ni ningún otro ajuste de la tarjeta: es
        la operación pensada para cuando el texto no convence y se quiere otra
        propuesta sin rehacer el resto.

        Se le pasa al modelo el texto anterior para que no repita lo mismo y,
        opcionalmente, una indicación del usuario («más corto», «otro
        enfoque»).
        """
        card = self.get_card(card_id)
        tweet = self.get_tweet(card["tweet_id"])
        params = dict(card.get("params") or {})

        prompt_tweet = dict(tweet)
        previous = {
            "top": params.get("top") or "",
            "bottom": params.get("bottom") or "",
            "caption": params.get("caption") or "",
        }
        if any(previous.values()):
            prompt_tweet["previous"] = previous
        if instructions and instructions.strip():
            prompt_tweet["instructions"] = instructions.strip()

        try:
            result = analysis_providers.analyse_tweet(prompt_tweet, provider)
        except Exception as exc:  # noqa: BLE001
            message = str(exc)[:400]
            self.store.log(
                f"Regeneración de texto fallida en la tarjeta {card_id}: {message}",
                level="error",
                tweet_id=card["tweet_id"],
            )
            raise DashboardError(message) from exc

        params["top"] = result["top"]
        params["bottom"] = result["bottom"]
        params["caption"] = result["caption"]
        params["hashtags"] = result["hashtags"]
        self.store.update_card(card_id, params=params)
        self.store.update_tweet(card["tweet_id"], analysis_json=result)

        self.store.log(
            f"Texto regenerado en la tarjeta {card_id} ({result['provider']})"
            + (f" con indicación: {instructions.strip()[:60]}" if instructions else ""),
            tweet_id=card["tweet_id"],
        )

        refreshed = self.get_card(card_id)
        if render:
            refreshed = self.render_card(card_id, params)
        return {
            "card": refreshed,
            "analysis": result,
            "text_only": True,
            "rendered": bool(render),
        }

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
