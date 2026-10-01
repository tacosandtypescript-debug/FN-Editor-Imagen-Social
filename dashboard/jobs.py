"""Cola de trabajos del dashboard.

Motivo: «Procesar» analiza con el CLI de Codex (unos 30 s) y compone una
tarjeta 4K. Tener al navegador esperando sesenta segundos es frágil: si el
móvil bloquea la pantalla, cambia de aplicación o la conexión parpadea, la
petición se corta, la interfaz muestra un fallo **aunque el servidor termine el
trabajo**. Es exactamente lo que ocurrió.

Con esta cola la petición responde al instante y el trabajo sigue por su
cuenta. Además acota la concurrencia: sin ella, diez clics seguidos lanzarían
diez procesos de Codex y diez composiciones 4K a la vez.
"""

from __future__ import annotations

import itertools
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from .clock import CLOCK

#: Estados de un trabajo.
QUEUED = "en_espera"
RUNNING = "en_curso"
DONE = "hecho"
FAILED = "fallido"


def _now() -> str:
    return CLOCK.now().replace(microsecond=0).isoformat()


@dataclass
class Job:
    """Un trabajo encolado."""

    id: int
    kind: str
    label: str
    state: str = QUEUED
    detail: str | None = None
    result: Any = None
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None

    @property
    def finished(self) -> bool:
        return self.state in {DONE, FAILED}

    def as_dict(self, include_result: bool = False) -> dict:
        data = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "state": self.state,
            "detail": self.detail,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
        if include_result:
            data["result"] = self.result
        return data


class JobQueue:
    """Cola FIFO atendida por uno o varios hilos.

    Se usa un número pequeño de trabajadores a propósito: el análisis con Codex
    y la composición 4K son pesados, y la gracia es que varios clics no
    desborden la máquina ni disparen el consumo de la suscripción.
    """

    def __init__(self, workers: int = 1, history: int = 40) -> None:
        self.workers = max(1, int(workers))
        self.history = max(4, int(history))
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._jobs: dict[int, Job] = {}
        self._order: list[int] = []
        self._payloads: dict[int, Callable[[], Any]] = {}
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        for index in range(self.workers):
            thread = threading.Thread(
                target=self._loop, name=f"dashboard-jobs-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def stop(self, timeout: float = 90.0) -> bool:
        """Detiene los trabajadores y espera a que terminen.

        La espera es generosa a propósito: un trabajo en curso (análisis con
        Codex o composición 4K) no se puede interrumpir a mitad, y volver antes
        de tiempo deja un hilo usando la base de datos mientras el proceso se
        cierra. Devuelve `True` si todos los trabajadores pararon.
        """
        self._stop.set()
        for _ in self._threads:
            self._queue.put(None)
        for thread in self._threads:
            thread.join(timeout=timeout)
        vivos = [thread.name for thread in self._threads if thread.is_alive()]
        if vivos:
            import sys

            print(
                f"aviso: seguían trabajando al cerrar: {', '.join(vivos)}",
                file=sys.stderr,
            )
        self._threads = []
        return not vivos

    @property
    def running(self) -> bool:
        return any(thread.is_alive() for thread in self._threads)

    # ------------------------------------------------------------------
    def submit(self, kind: str, label: str, func: Callable[[], Any]) -> Job:
        """Encola un trabajo y devuelve su ficha."""
        self.start()
        with self._lock:
            job = Job(id=next(self._ids), kind=kind, label=label)
            self._jobs[job.id] = job
            self._order.append(job.id)
            # Se recorta el historial para que no crezca sin límite.
            while len(self._order) > self.history:
                old = self._order.pop(0)
                if self._jobs.get(old) and self._jobs[old].finished:
                    self._jobs.pop(old, None)
                else:
                    self._order.insert(0, old)
                    break
        self._queue.put(job.id)
        self._payloads[job.id] = func
        return job

    def get(self, job_id: int) -> Job | None:
        with self._lock:
            return self._jobs.get(int(job_id))

    def wait(self, job_id: int, timeout: float = 300.0) -> Job | None:
        """Espera a que un trabajo termine. Pensado para pruebas y scripts."""
        import time

        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.get(job_id)
            if job is None or job.finished:
                return job
            time.sleep(0.1)
        return self.get(job_id)

    def state(self) -> dict:
        with self._lock:
            jobs = [self._jobs[i] for i in self._order if i in self._jobs]
            pending = sum(1 for job in jobs if job.state == QUEUED)
            running = sum(1 for job in jobs if job.state == RUNNING)
            recent = [job.as_dict() for job in reversed(jobs)][:12]
        return {
            "workers": self.workers,
            "threads_alive": self.running,
            "pending": pending,
            "running": running,
            "busy": bool(pending or running),
            "recent": recent,
        }

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._queue.get(timeout=1)
            except queue.Empty:
                continue
            if job_id is None:
                break
            func = self._payloads.pop(job_id, None)
            job = self.get(job_id)
            if job is None or func is None:
                continue
            job.state = RUNNING
            job.started_at = _now()
            try:
                job.result = func()
                job.state = DONE
            except Exception as exc:  # noqa: BLE001 - el trabajo nunca debe tumbar el hilo
                job.state = FAILED
                job.detail = str(exc)[:400]
            finally:
                job.finished_at = _now()
