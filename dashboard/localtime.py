"""Huso horario del usuario, resuelto de forma determinista.

Deducir el huso comparando el reloj de pared con la hora real no es fiable: en
esta máquina el reloj del sistema ha cambiado de configuración durante la
propia sesión (primero la zona estaba mal y el reloj bien; después al revés).
Por eso el huso se resuelve con reglas, no con lo que diga el sistema.

Orden de resolución:

1. `DASHBOARD_UTC_OFFSET`, si se ha fijado a mano. Es lo más predecible.
2. `zoneinfo` con la zona configurada (`DASHBOARD_TIMEZONE`), que aplica el
   horario de verano correcto. En Windows necesita el paquete `tzdata`.
3. Regla incorporada de horario de verano para la familia Eastern Time
   (Toronto, Montreal, Nueva York…), que es la de Quebec. No necesita nada.
4. Desfase que declare el sistema operativo, como último recurso.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

UTC = timezone.utc

DEFAULT_TIMEZONE = "America/Toronto"

#: Zonas que comparten la regla de horario de verano de Eastern Time.
EASTERN_ZONES = {
    "america/toronto",
    "america/montreal",
    "america/new_york",
    "america/detroit",
    "america/nassau",
    "america/iqaluit",
    "canada/eastern",
}

#: Desfase estándar y de verano de Eastern Time.
EASTERN_STANDARD = timedelta(hours=-5)
EASTERN_DAYLIGHT = timedelta(hours=-4)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Fecha del n-ésimo día de la semana del mes (weekday: lunes=0)."""
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    return first + timedelta(days=delta + 7 * (n - 1))


def eastern_offset(moment: datetime) -> timedelta:
    """Desfase de Eastern Time en ese instante: −4 en verano, −5 en invierno.

    El cambio se produce el segundo domingo de marzo a las 02:00 locales
    (07:00 UTC) y el primer domingo de noviembre a las 02:00 locales
    (06:00 UTC).
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    year = moment.year
    start = datetime.combine(nth_weekday(year, 3, 6, 2), time(7, 0), tzinfo=UTC)
    end = datetime.combine(nth_weekday(year, 11, 6, 1), time(6, 0), tzinfo=UTC)
    return EASTERN_DAYLIGHT if start <= moment < end else EASTERN_STANDARD


def _zoneinfo_offset(zone_name: str, moment: datetime) -> timedelta | None:
    """Desfase según la base de datos de zonas, si está disponible."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        return None
    for candidate in (zone_name, zone_name.replace(" ", "_")):
        try:
            return moment.astimezone(ZoneInfo(candidate)).utcoffset()
        except Exception:  # noqa: BLE001 - zona desconocida o sin tzdata
            continue
    return None


def resolve_offset(
    moment: datetime,
    *,
    timezone_name: str = DEFAULT_TIMEZONE,
    fixed_hours: float | None = None,
) -> tuple[timedelta, str]:
    """Devuelve el desfase local y de dónde sale, para poder explicarlo."""
    if fixed_hours is not None:
        return timedelta(hours=float(fixed_hours)), "configurado"

    zone = str(timezone_name or DEFAULT_TIMEZONE).strip().lower()
    from_zoneinfo = _zoneinfo_offset(timezone_name, moment)
    if from_zoneinfo is not None:
        return from_zoneinfo, f"zona {timezone_name}"

    if zone in EASTERN_ZONES:
        return eastern_offset(moment), "regla Eastern Time"

    # Sin base de datos de zonas y sin regla conocida: se usa el sistema.
    system = datetime.now().astimezone().utcoffset() or timedelta(0)
    return system, "sistema"


def describe(offset: timedelta) -> str:
    """Huso en formato corto: «UTC−4»."""
    total = int(round(offset.total_seconds()))
    sign = "-" if total < 0 else "+"
    hours, remainder = divmod(abs(total), 3600)
    minutes = remainder // 60
    if minutes:
        return f"UTC{sign}{hours}:{minutes:02d}"
    return f"UTC{sign}{hours}"
