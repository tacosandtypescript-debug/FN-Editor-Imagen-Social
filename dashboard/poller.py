"""Sondeo automático en segundo plano.

El hilo del sondeador **siempre** está activo, aunque se desactive el sondeo
periódico. Es importante: el botón «Buscar ahora» pide un sondeo en segundo
plano, y si el hilo no existiera esa petición se quedaría en nada —el aviso de
«sondeo iniciado» aparecería sin que ocurriera nada, y el navegador nunca se
abriría—. `periodic=False` solo significa «no sondees solo cada cierto
tiempo»; los sondeos pedidos a mano siguen funcionando.
"""

from __future__ import annotations

import threading

from . import config
from .service import DashboardService


class Poller:
    """Hilo de sondeo: atiende disparos manuales y, si toca, también periódicos."""

    def __init__(
        self,
        service: DashboardService,
        interval_seconds: int | None = None,
        periodic: bool = True,
    ) -> None:
        settings = config.Settings()
        self.service = service
        self.interval = max(60, int(interval_seconds or settings.poll_interval_seconds))
        self.periodic = periodic
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_run: dict | None = None
        self.last_error: str | None = None
        self.progress: dict = {
            "running": False,
            "done": 0,
            "total": 0,
            "current": None,
        }

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="dashboard-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def trigger(self) -> bool:
        """Pide un sondeo inmediato. Devuelve False si ya hay uno en curso."""
        if self._lock.locked():
            return False
        self._wake.set()
        return True

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        settings = config.Settings()
        if self.periodic and settings.poll_on_start:
            self._run_once()
        while not self._stop.is_set():
            # Sin sondeo periódico la espera es indefinida: el hilo queda
            # dormido hasta que alguien pida uno a mano.
            timeout = self.interval if self.periodic else None
            self._wake.wait(timeout=timeout)
            self._wake.clear()
            if self._stop.is_set():
                break
            self._run_once()

    def _run_once(self) -> None:
        if not self._lock.acquire(blocking=False):
            return
        try:
            accounts = self.service.store.list_accounts(active_only=True)
            if not accounts:
                self.last_error = None
                self.last_run = {"accounts": [], "new": 0, "skipped": "sin cuentas activas"}
                self.progress.update({"running": False, "done": 0, "total": 0, "current": None})
                return
            self.last_run = self.service.poll(progress=self.progress)
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - el hilo nunca debe morir
            self.last_error = str(exc)[:300]
            self.service.store.log(f"Sondeo automático fallido: {self.last_error}", level="error")
        finally:
            self.progress.update({"running": False, "current": None})
            self._lock.release()

    def status(self) -> dict:
        return {
            "running": self.running,
            "periodic": self.periodic,
            "interval_seconds": self.interval,
            "busy": self._lock.locked(),
            "last_run": self.last_run,
            "last_error": self.last_error,
            "progress": dict(self.progress),
        }
