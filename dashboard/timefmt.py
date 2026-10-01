"""Formato de fechas para la interfaz.

Se calcula en el servidor, no en el navegador, por dos razones: el reloj del
navegador es el mismo que puede estar desviado, y así el resultado se puede
probar con tests en lugar de confiar en que el JavaScript acierte.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

MONTHS_ES = (
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic",
)


def parse_moment(value) -> datetime | None:
    """Convierte lo que devuelven las fuentes en un datetime UTC."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, (int, float)):
        moment = datetime.fromtimestamp(float(value), timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            from email.utils import parsedate_to_datetime

            try:
                moment = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                return None
    else:
        return None

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def humanize_relative(value, now: datetime | None = None) -> str:
    """Devuelve «hace 2 h 15 min» a partir de una marca de tiempo.

    El detalle que importa: los minutos acompañan a las horas, que es lo que
    se pidió («hace una hora y tantos minutos»).
    """
    moment = parse_moment(value)
    if moment is None:
        return ""
    reference = now or datetime.now(timezone.utc)
    seconds = (reference - moment).total_seconds()

    # Una diferencia negativa solo puede venir de un reloj desviado o de una
    # publicación recién salida; no tiene sentido decir «en el futuro».
    if seconds < 0:
        seconds = 0

    if seconds < 60:
        return "hace unos segundos"

    minutes = int(seconds // 60)
    if minutes < 60:
        return f"hace {minutes} min"

    hours, remainder_minutes = divmod(minutes, 60)
    if hours < 24:
        return f"hace {hours} h {remainder_minutes} min" if remainder_minutes else f"hace {hours} h"

    days = int(seconds // 86400)
    if days == 1:
        return "hace 1 día"
    if days < 30:
        return f"hace {days} días"

    months = days // 30
    if months == 1:
        return "hace 1 mes"
    if months < 12:
        return f"hace {months} meses"

    years = days // 365
    return "hace 1 año" if years <= 1 else f"hace {years} años"


def format_absolute(value, offset_seconds: float = 0.0) -> str:
    """Fecha legible para poner al lado: «01 oct 2026 · 07:55».

    `offset_seconds` es el desfase real del usuario respecto a UTC, que puede
    no coincidir con la zona configurada en el sistema operativo.
    """
    moment = parse_moment(value)
    if moment is None:
        return ""
    local = moment + timedelta(seconds=offset_seconds)
    month = MONTHS_ES[local.month - 1]
    return f"{local.day:02d} {month} {local.year} · {local.hour:02d}:{local.minute:02d}"


def format_short_date(value, offset_seconds: float = 0.0) -> str:
    """Solo la fecha: «01/10/2026»."""
    moment = parse_moment(value)
    if moment is None:
        return ""
    local = moment + timedelta(seconds=offset_seconds)
    return f"{local.day:02d}/{local.month:02d}/{local.year}"


def decorate_tweet(tweet: dict, clock=None) -> dict:
    """Añade los campos de fecha ya formateados a una publicación.

    La hora relativa se calcula con la hora real medida, no con la del
    sistema, para que «hace X minutos» sea cierto aunque el reloj esté mal. El
    desfase local se pide por instante, porque el horario de verano cambia.
    """
    now = clock.now() if clock is not None else datetime.now(timezone.utc)

    posted = tweet.get("posted_at")
    fetched = tweet.get("fetched_at")
    reference = posted or fetched
    moment = parse_moment(reference) or now
    offset = clock.offset_for(moment).total_seconds() if clock is not None else 0.0

    tweet["posted_relative"] = humanize_relative(reference, now)
    tweet["posted_absolute"] = format_absolute(reference, offset)
    tweet["posted_short"] = format_short_date(reference, offset)
    tweet["date_is_estimated"] = not posted
    if not posted and tweet.get("relative_time"):
        # El navegador solo da la hora relativa que muestra X.
        tweet["posted_relative"] = f"hace {tweet['relative_time']}"
    return tweet


def describe_age_of_clock_check(measured_at_epoch: float | None, now_epoch: float) -> str:
    """Texto de cuándo se comprobó el reloj, para la interfaz."""
    if not measured_at_epoch:
        return "sin comprobar"
    seconds = max(0.0, now_epoch - measured_at_epoch)
    if seconds < 60:
        return "hace unos segundos"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"hace {minutes} min"
    hours = int(minutes // 60)
    return f"hace {hours} h"
