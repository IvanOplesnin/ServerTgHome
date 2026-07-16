from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from server_tg_home.core.config import Settings
from server_tg_home.database.models import SensorReading

METRIC_TITLES = {
    "temperature": "Температура",
    "humidity": "Влажность",
}


@dataclass(frozen=True)
class SensorMetricStats:
    room_id: str
    metric: str
    unit: str
    count: int
    latest_value: float
    latest_at: datetime
    min_value: float
    avg_value: float
    max_value: float


def build_sensor_analytics_text(
    settings: Settings,
    session: Session,
    *,
    room_id: str,
    window: timedelta,
    html: bool = False,
) -> str:
    end = datetime.now(UTC)
    start = end - window
    room_ids = list(settings.temperatures.rooms.keys()) if room_id == "all" else [room_id]
    stats = load_sensor_metric_stats(settings, session, room_ids=room_ids, start=start, end=end)

    title = f"Аналитика датчиков за {_format_window(window)}"
    lines = [_bold(title, html=html)]
    for current_room_id in room_ids:
        room = settings.temperatures.rooms.get(current_room_id)
        if room is None:
            continue
        lines.append("")
        lines.append(_escape(room.title, html=html))
        for metric in ("temperature", "humidity"):
            metric_stats = stats.get((current_room_id, metric))
            if metric_stats is None:
                lines.append(f"{METRIC_TITLES[metric]}: нет данных")
                continue
            lines.append(_format_metric(metric_stats, html=html))
    return "\n".join(lines)


def load_sensor_metric_stats(
    settings: Settings,
    session: Session,
    *,
    room_ids: list[str],
    start: datetime,
    end: datetime,
) -> dict[tuple[str, str], SensorMetricStats]:
    readings = session.execute(
        select(SensorReading)
        .where(
            SensorReading.room_id.in_(room_ids),
            SensorReading.metric.in_(["temperature", "humidity"]),
            SensorReading.recorded_at >= start,
            SensorReading.recorded_at <= end,
        )
        .order_by(SensorReading.recorded_at.asc())
    ).scalars().all()
    grouped: dict[tuple[str, str], list[SensorReading]] = {}
    for reading in readings:
        if reading.room_id not in settings.temperatures.rooms:
            continue
        grouped.setdefault((reading.room_id, reading.metric), []).append(reading)

    result: dict[tuple[str, str], SensorMetricStats] = {}
    for key, rows in grouped.items():
        values = [row.value for row in rows]
        latest = rows[-1]
        result[key] = SensorMetricStats(
            room_id=latest.room_id,
            metric=latest.metric,
            unit=latest.unit,
            count=len(rows),
            latest_value=latest.value,
            latest_at=_as_utc(latest.recorded_at),
            min_value=min(values),
            avg_value=mean(values),
            max_value=max(values),
        )
    return result


def _format_metric(stats: SensorMetricStats, *, html: bool) -> str:
    latest_age = datetime.now(UTC) - stats.latest_at
    value = _bold(f"{_format_number(stats.latest_value)} {stats.unit}", html=html)
    return (
        f"{METRIC_TITLES.get(stats.metric, stats.metric)}: {value} "
        f"({_format_age(latest_age)} назад)\n"
        f"  мин/сред/макс: "
        f"{_format_number(stats.min_value)} / {_format_number(stats.avg_value)} / "
        f"{_format_number(stats.max_value)} {stats.unit}; точек: {stats.count}"
    )


def _format_window(window: timedelta) -> str:
    seconds = int(window.total_seconds())
    if seconds % 86400 == 0:
        return f"{seconds // 86400} д"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} ч"
    if seconds % 60 == 0:
        return f"{seconds // 60} мин"
    return f"{seconds} сек"


def _format_age(age: timedelta) -> str:
    seconds = max(0, int(age.total_seconds()))
    if seconds < 60:
        return f"{seconds} сек"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} мин {seconds} сек"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч {minutes} мин"
    days, hours = divmod(hours, 24)
    return f"{days} д {hours} ч"


def _format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}"


def _bold(value: str, *, html: bool) -> str:
    escaped = _escape(value, html=html)
    return f"<b>{escaped}</b>" if html else escaped


def _escape(value: str, *, html: bool) -> str:
    return escape(value) if html else value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
