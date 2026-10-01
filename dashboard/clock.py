"""Reloj fiable para el dashboard.

Hay dos problemas independientes y conviene no confundirlos:

* **La hora.** El reloj del sistema puede estar desviado. Aquí se ha medido un
  desfase de seis horas. Se corrige midiendo la cabecera `Date` de servidores
  públicos, que es hora autoritativa y no una estimación.
* **El huso horario.** La zona configurada en el sistema puede no ser la del
  usuario, y en esta máquina ha llegado a cambiar durante la sesión. Se
  resuelve con reglas en `localtime.py`, no con lo que declare el sistema.

Con las dos cosas corregidas, «hace 20 minutos» y la fecha mostrada son
correctos aunque el reloj y la zona del equipo estén mal.
"""

from __future__ import annotations

import statistics
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen

from . import localtime

#: Servidores usados como referencia horaria. Se piden varios y se toma la
#: mediana para que un servidor con la hora mal no contamine el resultado.
DEFAULT_TIME_HOSTS = (
    "https://api.github.com",
    "https://www.google.com",
    "https://www.cloudflare.com",
    "https://www.bing.com",
)

TIMEOUT_SECONDS = 10
#: Cuánto vale una medida antes de volver a comprobarla.
DEFAULT_TTL_SECONDS = 1800
#: Si dos relojes discrepan más que esto, la medida no se considera fiable.
AGREEMENT_TOLERANCE_SECONDS = 5
#: Cuando la medición falla o solo hay un valor guardado, se reintenta antes.
FAILURE_TTL_SECONDS = 120


class Clock:
    """Reloj anclado a la hora de internet, no a la de la máquina.

    Tras la primera medición, la hora se calcula avanzando desde el instante
    de referencia con el reloj **monotónico**, que no se ve afectado por los
    cambios del reloj de pared. Así, si alguien toca la hora del sistema (o el
    sistema se resincroniza solo, como ha ocurrido aquí), este reloj no se
    desvía.
    """

    def __init__(
        self,
        hosts: tuple[str, ...] = DEFAULT_TIME_HOSTS,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        timezone_name: str = localtime.DEFAULT_TIMEZONE,
        fixed_offset_hours: float | None = None,
    ) -> None:
        self.hosts = tuple(hosts)
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.timezone_name = timezone_name
        self.fixed_offset_hours = fixed_offset_hours
        self._lock = threading.Lock()
        #: Ancla: hora real medida + lectura del reloj monotónico en ese momento.
        self._anchor_server_epoch: float | None = None
        self._anchor_monotonic: float | None = None
        #: Respaldo mientras no hay ancla (arranque sin red todavía).
        self._offset_seconds = 0.0
        self._measured_at: float | None = None
        self._source: str | None = None
        self._samples: list[float] = []
        self._agreeing_count = 0
        self._disagreeing: list[str] = []
        self._trusted = False

    # --- hora real -----------------------------------------------------
    def now(self) -> datetime:
        """Hora actual real, en UTC y con zona horaria.

        Con ancla, la hora sale de internet avanzada con el reloj monotónico:
        no interviene el reloj de pared de la máquina.
        """
        if self._anchor_monotonic is not None and self._anchor_server_epoch is not None:
            elapsed = time.monotonic() - self._anchor_monotonic
            return datetime.fromtimestamp(self._anchor_server_epoch + elapsed, timezone.utc)
        return datetime.now(timezone.utc) + timedelta(seconds=self._offset_seconds)

    @property
    def anchored(self) -> bool:
        """True si la hora ya no depende del reloj de la máquina."""
        return self._anchor_monotonic is not None

    def system_utc(self) -> datetime:
        """Lo que el sistema *cree* que es UTC. Solo informativo."""
        return datetime.now(timezone.utc)

    def wall_clock(self) -> datetime:
        """Reloj de pared sin zona: lo que se ve en la barra de tareas."""
        return datetime.now()

    @property
    def offset_seconds(self) -> float:
        return self._offset_seconds

    @property
    def trusted(self) -> bool:
        return self._trusted

    # --- huso horario --------------------------------------------------
    def offset_for(self, moment: datetime) -> timedelta:
        """Desfase local aplicable a un instante concreto (el DST cambia)."""
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        offset, _ = localtime.resolve_offset(
            moment,
            timezone_name=self.timezone_name,
            fixed_hours=self.fixed_offset_hours,
        )
        return offset

    def local_offset_source(self) -> str:
        _, source = localtime.resolve_offset(
            self.now(),
            timezone_name=self.timezone_name,
            fixed_hours=self.fixed_offset_hours,
        )
        return source

    @property
    def local_offset_seconds(self) -> float:
        return self.offset_for(self.now()).total_seconds()

    def set_local_offset_override(self, hours: float | None) -> None:
        self.fixed_offset_hours = None if hours is None else float(hours)

    def to_local(self, moment: datetime) -> datetime:
        """Convierte una marca UTC real a la hora local del usuario."""
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc) + self.offset_for(moment)

    # --- medición ------------------------------------------------------
    def is_stale(self) -> bool:
        if self._measured_at is None:
            return True
        # Un valor guardado o una medición fallida caducan antes, para no
        # quedarse con una hora equivocada durante media hora.
        ttl = self.ttl_seconds if self._trusted else FAILURE_TTL_SECONDS
        return (time.time() - self._measured_at) > ttl

    def ensure_fresh(self, force: bool = False) -> dict:
        """Mide si hace falta; nunca propaga el error de red."""
        if not force and not self.is_stale():
            return self.status()
        return self.measure()

    def measure(self) -> dict:
        """Mide la hora real contra los servidores de referencia."""
        samples: list[tuple[str, float]] = []
        failures: list[str] = []
        for host in self.hosts:
            try:
                samples.append((host, self._probe(host)))
            except Exception as exc:  # noqa: BLE001 - la red nunca debe romper el dashboard
                failures.append(f"{host}: {type(exc).__name__}")

        with self._lock:
            if not samples:
                # Sin muestras se conserva el ancla anterior: el reloj sigue
                # avanzando correctamente aunque la red falle ahora.
                self._measured_at = time.time()
                self._samples = []
                self._trusted = False
                return self.status(failures=failures)

            epochs = [value for _, value in samples]
            median_epoch = statistics.median(epochs)
            self._anchor_server_epoch = median_epoch
            self._anchor_monotonic = time.monotonic()
            self._offset_seconds = median_epoch - time.time()
            self._measured_at = time.time()
            self._samples = epochs
            self._source = samples[0][0]

            # Consenso por mediana: se comprueba cuántos servidores coinciden
            # con ella, en vez de mirar el máximo menos el mínimo. Así un
            # servidor con la hora algo desviada (GitHub iba 6 s por detrás de
            # Google, Cloudflare y Bing) no invalida una medición correcta.
            within = [value for value in epochs if abs(value - median_epoch) <= AGREEMENT_TOLERANCE_SECONDS]
            self._agreeing_count = len(within)
            self._disagreeing = [
                host for host, value in samples if abs(value - median_epoch) > AGREEMENT_TOLERANCE_SECONDS
            ]
            agreement = max(within) - min(within) if len(within) > 1 else 0.0
            self._trusted = len(within) >= 2 or len(epochs) == 1
            return self.status(failures=failures, agreement=agreement)

    def status(self, failures: list[str] | None = None, agreement: float | None = None) -> dict:
        offset = self.offset_for(self.now())
        return {
            "offset_seconds": round(self._offset_seconds, 1),
            "offset_human": describe_offset(self._offset_seconds),
            "trusted": self._trusted,
            "measured": self._measured_at is not None,
            "anchored": self.anchored,
            "independent_of_system_clock": self.anchored,
            "stale": self.is_stale(),
            "source": self._source,
            "host_count": len(self.hosts),
            "sample_count": len(self._samples),
            "agreeing_count": self._agreeing_count,
            "disagreeing_hosts": list(self._disagreeing),
            "agreement_seconds": round(agreement, 2) if agreement is not None else None,
            "failures": failures or [],
            "local_offset_seconds": round(offset.total_seconds(), 1),
            "local_offset_human": localtime.describe(offset),
            "local_offset_source": self.local_offset_source(),
            "timezone_name": self.timezone_name,
            "system_utc": self.system_utc().replace(microsecond=0).isoformat(),
            "wall_clock": self.wall_clock().replace(microsecond=0).isoformat(),
        }

    def set_offset(self, seconds: float, source: str = "guardado", trusted: bool = True) -> None:
        """Restaura un desfase medido antes, para el arranque sin red.

        No se crea ancla: la lectura monotónica de un proceso anterior no sirve
        en este. Se usa solo como respaldo hasta la primera medición.
        """
        with self._lock:
            self._offset_seconds = float(seconds)
            self._source = source
            self._trusted = trusted
            # Se marca como medido hace poco para no repetir la consulta.
            self._measured_at = time.time()

    # ------------------------------------------------------------------
    def _probe(self, host: str) -> float:
        """Estima la hora real (epoch) a partir de la cabecera `Date`.

        Se compensa la mitad del tiempo de ida y vuelta, porque la cabecera
        tiene resolución de un segundo y la respuesta tarda en llegar.
        """
        request = Request(host, headers={"User-Agent": "EditImg-Dashboard/0.1"}, method="GET")
        started = time.time()
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            header = response.headers.get("Date")
            response.read(1)  # cierra la conexión cuanto antes
        finished = time.time()
        if not header:
            raise ValueError("sin cabecera Date")
        server_time = parsedate_to_datetime(header)
        if server_time.tzinfo is None:
            server_time = server_time.replace(tzinfo=timezone.utc)
        return server_time.timestamp() + (finished - started) / 2.0


def describe_offset(seconds: float) -> str:
    """Texto legible del desfase del reloj, para mostrarlo en la interfaz.

    `seconds` es lo que va el servidor de referencia por delante del reloj
    local, así que un valor positivo significa que el reloj va **atrasado**.
    """
    if abs(seconds) < 5:
        return "reloj correcto"
    total = abs(int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    parts = []
    if hours:
        parts.append(f"{hours} h")
    if minutes or not hours:
        parts.append(f"{minutes} min")
    direction = "atrasado" if seconds > 0 else "adelantado"
    return f"reloj del sistema {direction} {(' ').join(parts)}"


#: Instancia compartida por todo el dashboard.
CLOCK = Clock()
