"""Capa de servicio del dashboard.

El dashboard es un **visor**: descubre publicaciones, las enseña y deja
marcarlas como listas. Aquí vive esa lógica, sin nada de HTTP, para poder
probarla sola.

Ya no analiza, ni compone tarjetas, ni entrega nada: eso se quitó entero. La
composición de tarjetas sigue viviendo en `bin/` del repositorio, que este
dashboard ya no usa.
"""

from __future__ import annotations

from . import config
from . import posts
from . import timefmt
from . import urls
from .clock import CLOCK
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
        #: Se acepta `workers` por compatibilidad con quien ya construía el
        #: servicio así; ya no hay cola de trabajos que dimensionar.
        self.workers = max(1, int(workers))

    def shutdown(self) -> None:
        """Nada que parar: el sondeo corre en su propio hilo (ver `poller`)."""

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
            "timeline_providers": [
                status.__dict__ for status in timeline_providers.available_providers()
            ],
            "profiles": timeline_providers.profile_directories(),
            "clock": self.clock_status(),
            "maintenance": self.maintenance_state(),
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
        """Añade una cuenta, validando antes el nombre.

        Sin esta comprobación un handle vacío llegaba a la base de datos y el
        fallo salía como error interno 500 en lugar de un 400 que explique qué
        falta.
        """
        limpio = store_module.normalise_handle(str(handle or ""))
        if not limpio:
            raise DashboardError(
                "hace falta un nombre de cuenta: escribe @cuenta o su enlace de X"
            )
        try:
            account = self.store.add_account(limpio)
        except Exception as exc:  # noqa: BLE001 - se muestra el motivo real
            raise DashboardError(str(exc)[:200]) from exc
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
                info = posts.probe_post(url)
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
        tweets = [
            timefmt.decorate_tweet(tweet, CLOCK) for tweet in self.store.list_tweets(**kwargs)
        ]
        for tweet in tweets:
            self._decorate_media(tweet)
        return tweets

    @staticmethod
    def _decorate_media(tweet: dict) -> dict:
        """Añade las miniaturas reducidas y marca cuáles son vídeo.

        La lista `media` se conserva tal cual; `thumbs` pide al CDN una versión
        de 360 px, que pesa siete veces menos. Los vídeos de X se anuncian con
        una miniatura (`amplify_video_thumb`), así que se detectan por la URL
        para poder señalarlos y enlazarlos.
        """
        medios = list(tweet.get("media") or [])
        tweet["thumbs"] = urls.thumbnails(medios)
        tweet["has_video"] = any("amplify_video" in str(url) for url in medios)
        return tweet

    def mark_ready(self, tweet_id: str) -> dict:
        """Marca una publicación como lista para que la limpieza no la borre.

        La retención borra todo lo anterior a 48 h, sin mirar el estado. Esta
        marca es la excepción: es la forma de decir «esta ya la vi y la quiero
        conservar».
        """
        tweet = self.get_tweet(tweet_id)
        self.store.set_tweet_status(tweet_id, store_module.STATUS_READY)
        self.store.log(f"Marcada como lista: {tweet_id}", tweet_id=tweet_id)
        return self.get_tweet(tweet_id)

    def unmark_ready(self, tweet_id: str) -> dict:
        """Devuelve una publicación marcada al montón, para poder limpiarla."""
        tweet = self.get_tweet(tweet_id)
        self.store.set_tweet_status(tweet_id, store_module.STATUS_NEW)
        self.store.log(f"Desmarcada: {tweet_id}", tweet_id=tweet_id)
        return self.get_tweet(tweet_id)

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
