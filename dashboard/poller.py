"""Sondeo automático en segundo plano.

Un hilo recorre las cuentas activas cada `poll_interval_seconds`. Se puede
lanzar un sondeo manual desde la interfaz sin esperar al intervalo; ambos
comparten un cerrojo para no solaparse.
"""

from __future__ import annotations

import threading
import time

from . import config
from .service import DashboardService


class Poller:
    """Hilo de sondeo periódico."""

    def __init__(self, service: DashboardService, interval_seconds: int | None = None) -> None:
        settings = config.Settings()
        self.service = service
        self.interval = max(60, int(interval_seconds or settings.poll_interval_seconds))
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_run: dict | None = None
        self.last_error: str | None = None

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
        if settings.poll_on_start:
            self._run_once()
        while not self._stop.is_set():
            # Espera al intervalo, pero se despierta antes si se pide a mano.
            self._wake.wait(timeout=self.interval)
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
                return
            self.last_run = self.service.poll()
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - el hilo nunca debe morir
            self.last_error = str(exc)[:300]
            self.service.store.log(f"Sondeo automático fallido: {self.last_error}", level="error")
        finally:
            self._lock.release()

    def status(self) -> dict:
        return {
            "running": self.running,
            "interval_seconds": self.interval,
            "busy": self._lock.locked(),
            "last_run": self.last_run,
            "last_error": self.last_error,
        }


def background_poll_once(service: DashboardService) -> None:
    """Utilidad para lanzar un sondeo suelto sin bloquear la petición HTTP."""
    threading.Thread(target=_safe_poll, args=(service,), daemon=True).start()


def _safe_poll(service: DashboardService) -> None:
    try:
        service.poll()
    except Exception:  # noqa: BLE001 - se registra dentro de poll
        pass


def sleep(seconds: float) -> None:
    time.sleep(seconds)
