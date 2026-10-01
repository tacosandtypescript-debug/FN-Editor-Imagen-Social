"""Capa de servicio del dashboard.

Aquí vive la lógica del flujo: descubrir, analizar, componer y entregar. No
sabe nada de HTTP, así que se puede probar sola y reutilizar desde otro
frontend si algún día hace falta.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import config
from . import previews
from . import timefmt
from . import urls
from .clock import CLOCK
from .jobs import JobQueue
from .pipeline import cards as cards_pipeline
from .pipeline import media as media_pipeline
from .providers import analysis as analysis_providers
from .providers import telegram as telegram_providers
from .providers import timelines as timeline_providers
from .store import Store
from . import store as store_module

#: Máximo de publicaciones que se enriquecen **por cuenta** en un sondeo.
ENRICH_LIMIT = 25

#: Máximo de consultas de enriquecido en un sondeo completo, sumando todas las
#: cuentas. Sin este tope, 24 cuentas por 25 darían hasta 600 peticiones
#: seguidas a los mirrors públicos, que responden 429.
ENRICH_BUDGET = 60

#: Por debajo de esto, el navegador está devolviendo la vista previa de X y no
#: el perfil completo, casi siempre por falta de sesión iniciada.
BROWSER_PREVIEW_LIMIT = 6


class DashboardError(RuntimeError):
    """Error de negocio con mensaje apto para mostrar en la interfaz."""


class DashboardService:
    """Orquesta el flujo completo sobre el repositorio existente."""

    def __init__(self, store: Store | None = None, workers: int = 1) -> None:
        config.ensure_directories()
        self.store = store or Store()
        #: «Procesar» es pesado (Codex ~30 s + composición 4K). Se atiende en
        #: segundo plano para que el navegador no tenga que esperar: si el
        #: móvil corta la petición, el trabajo se pierde aunque el servidor
        #: hubiera terminado bien.
        self.jobs = JobQueue(workers=workers)

    def shutdown(self) -> None:
        self.jobs.stop()

    def jobs_state(self) -> dict:
        return self.jobs.state()

    def list_cards_view(self, limit: int = 200) -> list[dict]:
        """Todas las tarjetas creadas, con su publicación y su última entrega.

        Es la vista que faltaba: al procesar, la publicación sale de la bandeja
        de pendientes y no había ningún sitio donde volver a verla para
        enviarla o cambiarle el texto.
        """
        out: list[dict] = []
        for card in self.store.list_cards()[: max(1, int(limit))]:
            tweet = self.store.get_tweet(card["tweet_id"])
            resumen: dict = {}
            if tweet:
                decorated = self._decorate_media(timefmt.decorate_tweet(dict(tweet), CLOCK))
                resumen = {
                    key: decorated.get(key)
                    for key in (
                        "tweet_id",
                        "author_handle",
                        "source_handle",
                        "text",
                        "url",
                        "status",
                        "posted_relative",
                        "posted_absolute",
                        "thumbs",
                        "media",
                    )
                }
            deliveries = self.store.list_deliveries(card["id"], limit=1)
            out.append(
                {
                    "card": card,
                    "tweet": resumen,
                    "last_delivery": deliveries[0] if deliveries else None,
                    "sent": bool(deliveries and deliveries[0].get("status") == "ok"),
                    "editor_url": f"/editor.html?card={card['id']}",
                }
            )
        return out

    def enqueue_regenerate(
        self,
        card_id: int,
        instructions: str | None = None,
        provider: str | None = None,
        render: bool = True,
    ) -> dict:
        """Encola la regeneración del texto de una tarjeta ya creada."""
        card = self.get_card(card_id)
        label = f"texto de la tarjeta {card_id}"

        def trabajo() -> dict:
            return self.regenerate_text(
                card_id, instructions=instructions, render=render, provider=provider
            )

        job = self.jobs.submit("regenerar", label, trabajo)
        self.store.log(
            f"Encolada la regeneración de texto de la tarjeta {card_id} (trabajo {job.id})",
            tweet_id=card["tweet_id"],
        )
        return job.as_dict()

    def enqueue_send(
        self, card_id: int, caption: str | None = None, provider: str | None = None
    ) -> dict:
        """Encola la entrega de una tarjeta.

        La subida puede tardar: el PNG sin comprimir ronda los 30 MB y por una
        conexión móvil eso no cabe en una petición que el navegador espere.
        """
        card = self.get_card(card_id)
        label = f"envío de la tarjeta {card_id}"

        def trabajo() -> dict:
            return self.send_card(card_id, caption=caption, provider=provider)

        job = self.jobs.submit("enviar", label, trabajo)
        self.store.log(
            f"Encolada la entrega de la tarjeta {card_id} (trabajo {job.id})",
            tweet_id=card["tweet_id"],
        )
        return job.as_dict()

    def enqueue_process(
        self,
        tweet_id: str,
        params: dict | None = None,
        provider: str | None = None,
        force_analysis: bool = False,
    ) -> dict:
        """Encola el procesado de una publicación y responde al instante."""
        tweet = self.get_tweet(tweet_id)
        if not tweet.get("media"):
            raise DashboardError(
                "la publicación no tiene imágenes: el compositor necesita al menos una"
            )

        handle = tweet.get("author_handle") or tweet.get("source_handle") or ""
        label = f"@{handle} · {tweet_id}"

        def trabajo() -> dict:
            try:
                return self.process_tweet(tweet_id, params, provider, force_analysis)
            except Exception as exc:  # noqa: BLE001
                # La publicación no debe quedarse en «procesando» para siempre.
                message = str(exc)[:400]
                self.store.set_tweet_status(
                    tweet_id, store_module.STATUS_FAILED, detail=message
                )
                raise

        self.store.set_tweet_status(tweet_id, store_module.STATUS_PROCESSING)
        job = self.jobs.submit("procesar", label, trabajo)
        self.store.log(f"Encolado el procesado de {tweet_id} (trabajo {job.id})", tweet_id=tweet_id)
        return job.as_dict()

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
            "analysis_status": self.analysis_status(),
            "maintenance": self.maintenance_state(),
            "jobs": self.jobs_state(),
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
            progress.update(
                {"total": len(handles), "done": 0, "current": None, "running": True, "provider": None}
            )

        def report(payload: dict) -> None:
            """Refleja el avance del proveedor mientras trabaja.

            El lote del navegador tarda minutos: si no se informa cuenta a
            cuenta, la interfaz parece congelada en 0 de 24.
            """
            if progress is None:
                return
            if payload.get("provider"):
                progress["provider"] = payload["provider"]
            if payload.get("total"):
                progress["total"] = payload["total"]
            if payload.get("handle"):
                progress["current"] = payload["handle"]
            if payload.get("index") is not None:
                progress["done"] = max(0, int(payload["index"]) - 1)

        try:
            batch = timeline_providers.fetch_timelines_batch(
                handles, provider, on_progress=report
            )
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
        # Presupuesto compartido por todo el sondeo: sin él, cada cuenta podría
        # pedir hasta 25 enriquecidos y 24 cuentas dispararían 600 peticiones
        # seguidas a los mirrors públicos.
        settings = config.Settings()
        budget = {"remaining": max(0, int(settings.enrich_budget)), "skipped": 0}
        for position, account in enumerate(accounts, 1):
            name = account["handle"]
            if progress is not None:
                progress["current"] = name
                progress["done"] = position
            outcome = batch["results"].get(name)
            if isinstance(outcome, Exception):
                one = self._record_failure(name, str(outcome))
            else:
                one = self._record_success(
                    name, outcome, provider_name, batch.get("attempts"), budget
                )
            total_new += one["new"]
            results.append(one)

        if budget["skipped"]:
            self.store.log(
                f"Se dejaron {budget['skipped']} publicación(es) sin consultar a los "
                f"mirrors por el límite de {settings.enrich_budget} por sondeo; se "
                "completarán en el siguiente. Sube DASHBOARD_ENRICH_BUDGET para "
                "hacerlo todo de una vez.",
                level="warn",
            )

        if progress is not None:
            progress.update({"current": None, "running": False, "provider": provider_name})
        # Tras cada sondeo se aprovecha para limpiar lo que ya caducó.
        purge = self.maintenance()
        self.store.log(
            f"Sondeo terminado: {total_new} publicación(es) nueva(s) en {len(accounts)} cuenta(s) "
            f"vía {provider_name}"
        )
        return {
            "accounts": results,
            "new": total_new,
            "provider": provider_name,
            "purge": purge,
        }

    def _record_success(
        self,
        handle: str,
        tweets: list[dict],
        provider_name: str | None,
        attempts: list[dict] | None = None,
        budget: dict | None = None,
    ) -> dict:
        latest = max((tweet["tweet_id"] for tweet in tweets), default=None)
        # Se enriquecen solo las que nunca se han visto: el registro permanente
        # evita repetir consultas a los mirrors tras una limpieza.
        unseen = set(self.store.filter_unseen(tweet["tweet_id"] for tweet in tweets))
        # Y solo las que no traen ya la fecha y los medios. Nitter entrega las
        # dos cosas en su RSS, así que preguntar otra vez por ellas era gastar
        # una petición por publicación (0,34 s) y arriesgarse al 429.
        #
        # El filtro va ANTES del tope: al revés, las que necesitan enriquecido
        # y caían más allá del puesto 25 se quedaban sin fecha para siempre.
        pending = [
            tweet
            for tweet in tweets
            if tweet["tweet_id"] in unseen
            and not (tweet.get("posted_at") and tweet.get("media"))
        ]
        allowed = min(ENRICH_LIMIT, len(pending))
        if budget is not None:
            allowed = min(allowed, max(0, int(budget.get("remaining", 0))))
        selected = pending[:allowed]
        if budget is not None:
            budget["remaining"] = max(0, int(budget.get("remaining", 0)) - len(selected))
            budget["skipped"] = int(budget.get("skipped", 0)) + (len(pending) - len(selected))
        self._enrich(selected)

        settings = config.Settings()
        result = self.store.upsert_tweets(
            tweets, duplicate_window_days=settings.duplicate_window_days
        )
        inserted = len(result["inserted"])
        repeated = len(result["content_duplicates"])
        self.store.mark_account_checked(
            handle, error=None, last_post_id=str(latest) if latest else None
        )
        note = ""
        if attempts:
            note = " (con respaldo: " + "; ".join(
                f"{item['provider']} falló" for item in attempts
            ) + ")"
        replay = f", {repeated} repetida(s) por contenido" if repeated else ""
        self.store.log(
            f"@{handle}: {inserted} nueva(s), {result['duplicates']} ya vista(s){replay} "
            f"vía {provider_name}{note}"
        )
        # X, sin sesión iniciada, solo muestra una vista previa de cada perfil
        # (unas cinco publicaciones) y no carga más por mucho que se desplace.
        # Conviene decirlo en vez de aceptar el resultado truncado en silencio.
        if str(provider_name or "").startswith("browser") and len(tweets) <= BROWSER_PREVIEW_LIMIT:
            self.store.log(
                f"@{handle}: el navegador solo devolvió {len(tweets)} publicación(es). "
                "Sin sesión iniciada, X corta la vista previa del perfil; Nitter da "
                "muchas más. Inicia sesión en el perfil del navegador si quieres "
                "usarlo como fuente principal.",
                level="warn",
                tweet_id=None,
            )
        return {
            "handle": handle,
            "ok": True,
            "new": inserted,
            "duplicates": result["duplicates"],
            "content_duplicates": repeated,
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
            self._decorate_media(tweet)
            card_id = cards.get(tweet["tweet_id"])
            tweet["card_id"] = card_id
            tweet["has_card"] = card_id is not None
            if card_id:
                tweet["editor_url"] = f"/editor.html?card={card_id}"
        return tweets

    @staticmethod
    def _decorate_media(tweet: dict) -> dict:
        """Añade la lista de miniaturas reducidas junto a los medios originales.

        La lista `media` se conserva tal cual porque es la que se usa al
        componer; `thumbs` es solo para mostrar, y pesa siete veces menos.
        Devuelve el propio diccionario para poder encadenar la llamada.
        """
        tweet["thumbs"] = urls.thumbnails(tweet.get("media") or [])
        return tweet

    def maintenance(self, force: bool = False) -> dict:
        """Limpia la bandeja según la retención configurada.

        Borra las publicaciones más antiguas que `retention_hours` junto con sus
        medios y tarjetas. **No** toca el registro de «ya vistas»: por eso una
        publicación limpiada no reaparece en la siguiente búsqueda.
        """
        settings = config.Settings()
        if not force and not self._purge_due(settings.retention_hours):
            return {"skipped": True, "reason": "todavía no toca limpiar"}

        outcome = self.store.purge_older_than(settings.retention_hours, config.MEDIA_DIR)
        self.store.set_setting("last_purge_at", self.now().replace(microsecond=0).isoformat())
        # Las miniaturas reducidas también se podan: se regeneran solas si
        # hicieran falta, así que no hay riesgo en borrarlas.
        try:
            outcome["previews_removed"] = previews.prune()
        except Exception:  # noqa: BLE001 - la limpieza no debe tumbar el sondeo
            outcome["previews_removed"] = 0
        if outcome["purged"]:
            self.store.log(
                f"Limpieza: {outcome['purged']} publicación(es) de más de "
                f"{settings.retention_hours} h, {outcome['files_removed']} archivo(s) borrados. "
                f"Se recuerdan {outcome['seen_kept']} vistas para no repetirlas."
            )
        return outcome

    def _purge_due(self, retention_hours: int) -> bool:
        """Como mucho una limpieza por hora, para no repetirla en cada sondeo."""
        last = self.store.get_setting("last_purge_at")
        if not last:
            return True
        moment = timefmt.parse_moment(last)
        if moment is None:
            return True
        return (self.now() - moment).total_seconds() >= 3600

    def maintenance_state(self) -> dict:
        settings = config.Settings()
        seen = self.store.seen_stats()
        return {
            "retention_hours": settings.retention_hours,
            "duplicate_window_days": settings.duplicate_window_days,
            "last_purge_at": self.store.get_setting("last_purge_at"),
            "total_seen": seen["total_seen"],
            "oldest_seen_at": seen["oldest_seen_at"],
            "purge_due": self._purge_due(settings.retention_hours),
        }

    def get_tweet(self, tweet_id: str) -> dict:
        tweet = self.store.get_tweet(tweet_id)
        if not tweet:
            raise DashboardError(f"no existe la publicación {tweet_id}")
        decorated = timefmt.decorate_tweet(tweet, CLOCK)
        self._decorate_media(decorated)
        return decorated

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
        force_analysis: bool = False,
    ) -> dict:
        """Prepara una publicación de principio a fin, en un solo paso.

        Es lo que dispara el botón «Procesar»: analiza si todavía no hay
        análisis, descarga los medios y compone la tarjeta. Deja el resultado
        listo para abrir en el editor independiente.

        El análisis se rehace cuando el guardado es de relleno (`manual`) o de
        otro proveedor distinto del configurado: si no, un análisis antiguo
        hecho sin IA impediría que ChatGPT redactara los textos y los colores.
        """
        tweet = self.get_tweet(tweet_id)
        if not tweet.get("media"):
            raise DashboardError(
                "la publicación no tiene imágenes: el compositor necesita al menos una"
            )

        done: list[str] = []
        existing = tweet.get("analysis") or {}
        current = str(existing.get("provider") or "").strip().lower()
        configured = (
            provider or config.Settings().analysis_provider or ""
        ).strip().lower()
        needs_analysis = (
            force_analysis
            or not existing
            or current in {"", "manual"}
            or (configured and current != configured)
        )
        if needs_analysis:
            self.analyse(tweet_id, provider)
            done.append("análisis de texto")
        else:
            done.append("análisis ya existente")

        card = self.prepare_card(tweet_id, params)
        done.append("descarga de medios y composición")

        return {
            "card": card,
            "tweet": self.get_tweet(tweet_id),
            "steps": done,
            "editor_url": f"/editor.html?card={card['id']}",
        }

    def test_analysis(self, tweet_id: str, provider: str | None = None) -> dict:
        """Prueba el análisis sin guardarlo ni tocar la tarjeta.

        Sirve para comprobar que el proveedor responde (por ejemplo, que la
        sesión de ChatGPT está iniciada) antes de procesar nada.
        """
        tweet = self.get_tweet(tweet_id)
        try:
            result = analysis_providers.analyse_tweet(tweet, provider)
        except Exception as exc:  # noqa: BLE001
            message = str(exc)[:500]
            self.store.log(f"Prueba de análisis fallida: {message}", level="error", tweet_id=tweet_id)
            raise DashboardError(message) from exc
        self.store.log(
            f"Prueba de análisis correcta con {result.get('provider')}", tweet_id=tweet_id
        )
        return {"analysis": result, "tweet_id": tweet_id, "saved": False}

    def open_chatgpt_login(self) -> dict:
        """Abre una ventana de Chrome con el perfil de ChatGPT para entrar."""
        provider = analysis_providers.BrowserChatGPTAnalysis()
        try:
            outcome = provider.open_login_window()
        except Exception as exc:  # noqa: BLE001
            raise DashboardError(str(exc)[:400]) from exc
        self.store.log("Ventana de ChatGPT abierta para iniciar sesión")
        return outcome

    def analysis_status(self) -> dict:
        """Estado de los proveedores de análisis, para la interfaz."""
        from .providers import codex_cli

        provider = analysis_providers.BrowserChatGPTAnalysis()
        executable = codex_cli.find_codex_executable()
        return {
            "chatgpt_profile": str(provider.profile),
            "chatgpt_logged_in": provider.profile_logged_in(),
            "codex_executable": executable,
            "codex_auth_mode": codex_cli.auth_mode(),
            "codex_candidates": codex_cli.candidate_paths()[:5],
            "providers": [status.__dict__ for status in analysis_providers.available_providers()],
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
        self.store.mark_processed(tweet_id)
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
