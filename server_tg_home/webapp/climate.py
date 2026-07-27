from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
import math
import re

from sqlalchemy import BigInteger, cast, func, select
from sqlalchemy.orm import Session

from server_tg_home.core.config import Settings
from server_tg_home.core.temperatures import get_humidity, get_temperature
from server_tg_home.database.models import SensorReading
from server_tg_home.webapp.schemas import (
    ClimateCurrent,
    ClimateHistory,
    ClimateHistoryPoint,
    ClimateHistorySeries,
    ClimateReading,
    ClimateRoom,
)

DEFAULT_HISTORY_WINDOW = timedelta(hours=24)
HARD_MAX_HISTORY_WINDOW = timedelta(days=31)
DEFAULT_HISTORY_POINT_LIMIT = 500
MIN_HISTORY_POINT_LIMIT = 10
MAX_HISTORY_POINT_LIMIT = 2_000
SENSOR_METRICS = ("temperature", "humidity")
BUCKET_PATTERN = re.compile(r"^(?P<amount>[1-9]\d{0,5})(?P<unit>[smhd])$")
NICE_BUCKET_SECONDS = (
    1,
    5,
    10,
    30,
    60,
    5 * 60,
    15 * 60,
    30 * 60,
    60 * 60,
    3 * 60 * 60,
    6 * 60 * 60,
    12 * 60 * 60,
    24 * 60 * 60,
    2 * 24 * 60 * 60,
    7 * 24 * 60 * 60,
    14 * 24 * 60 * 60,
    31 * 24 * 60 * 60,
)


class UnknownClimateRoom(LookupError):
    pass


class InvalidClimateHistory(ValueError):
    pass


def get_current_climate(
    settings: Settings,
    session: Session,
    *,
    now: datetime | None = None,
) -> ClimateCurrent:
    generated_at = _as_utc(now or datetime.now(UTC))
    rooms: list[ClimateRoom] = []
    for room_id, room_config in settings.temperatures.rooms.items():
        temperature = get_temperature(session, settings, room_id)
        humidity = get_humidity(session, settings, room_id)
        rooms.append(
            ClimateRoom(
                id=room_id,
                title=room_config.title,
                temperature=(
                    ClimateReading(
                        value=temperature.temperature,
                        unit=temperature.unit,
                        updated_at=temperature.updated_at,
                        stale=_is_stale(
                            temperature.updated_at,
                            generated_at,
                            settings.temperatures.stale_after_sec,
                        ),
                    )
                    if temperature is not None
                    else None
                ),
                humidity=(
                    ClimateReading(
                        value=humidity.humidity,
                        unit=humidity.unit,
                        updated_at=humidity.updated_at,
                        stale=_is_stale(
                            humidity.updated_at,
                            generated_at,
                            settings.temperatures.stale_after_sec,
                        ),
                    )
                    if humidity is not None
                    else None
                ),
            )
        )
    return ClimateCurrent(rooms=rooms, generated_at=generated_at)


def get_climate_history(
    settings: Settings,
    session: Session,
    *,
    room_id: str,
    from_: datetime | None = None,
    to: datetime | None = None,
    bucket: str = "auto",
    point_limit: int = DEFAULT_HISTORY_POINT_LIMIT,
    now: datetime | None = None,
) -> ClimateHistory:
    if room_id not in settings.temperatures.rooms:
        raise UnknownClimateRoom(f"Unknown climate room: {room_id}")
    if not MIN_HISTORY_POINT_LIMIT <= point_limit <= MAX_HISTORY_POINT_LIMIT:
        raise InvalidClimateHistory(
            f"point_limit must be between {MIN_HISTORY_POINT_LIMIT} and {MAX_HISTORY_POINT_LIMIT}"
        )

    current_time = _as_utc(now or datetime.now(UTC))
    end = _as_utc(to) if to is not None else current_time
    start = _as_utc(from_) if from_ is not None else end - DEFAULT_HISTORY_WINDOW
    if start >= end:
        raise InvalidClimateHistory("'from' must be earlier than 'to'")

    max_window = _max_history_window(settings)
    span = end - start
    if span > max_window:
        raise InvalidClimateHistory(
            f"history window must not exceed {int(max_window.total_seconds())} seconds"
        )

    requested_bucket_sec = _parse_bucket(bucket)
    minimum_bucket_sec = max(1, math.ceil(span.total_seconds() / max(point_limit - 1, 1)))
    effective_bucket_sec = _nice_bucket(
        max(minimum_bucket_sec, requested_bucket_sec or 1)
    )

    rows = _load_bucketed_history(
        session,
        room_id=room_id,
        start=start,
        end=end,
        bucket_sec=effective_bucket_sec,
    )
    grouped: dict[str, list[ClimateHistoryPoint]] = {
        metric: [] for metric in SENSOR_METRICS
    }
    units: dict[str, str] = {}
    for metric, bucket_index, average, sample_count, unit in rows:
        if metric not in grouped:
            continue
        grouped[metric].append(
            ClimateHistoryPoint(
                timestamp=datetime.fromtimestamp(
                    int(bucket_index) * effective_bucket_sec,
                    UTC,
                ),
                value=float(average),
                sample_count=int(sample_count),
            )
        )
        units[metric] = str(unit)

    series = [
        ClimateHistorySeries(
            metric=metric,
            unit=units.get(metric) or _default_metric_unit(settings, metric),
            points=_limit_points(grouped[metric], point_limit),
        )
        for metric in SENSOR_METRICS
    ]
    return ClimateHistory(
        from_=start,
        to=end,
        room_id=room_id,
        bucket_sec=effective_bucket_sec,
        series=series,
    )


def _load_bucketed_history(
    session: Session,
    *,
    room_id: str,
    start: datetime,
    end: datetime,
    bucket_sec: int,
) -> list[tuple[str, int, float, int, str]]:
    dialect_name = session.get_bind().dialect.name
    if dialect_name == "sqlite":
        epoch = cast(func.strftime("%s", SensorReading.recorded_at), BigInteger)
        bucket_index = cast(epoch / bucket_sec, BigInteger)
    else:
        epoch = func.extract("epoch", SensorReading.recorded_at)
        bucket_index = cast(func.floor(epoch / bucket_sec), BigInteger)

    bucket_label = bucket_index.label("bucket_index")
    query = (
        select(
            SensorReading.metric,
            bucket_label,
            func.avg(SensorReading.value),
            func.count(SensorReading.id),
            func.max(SensorReading.unit),
        )
        .where(
            SensorReading.room_id == room_id,
            SensorReading.metric.in_(SENSOR_METRICS),
            SensorReading.recorded_at >= start,
            SensorReading.recorded_at < end,
        )
        .group_by(SensorReading.metric, bucket_index)
        .order_by(bucket_index.asc(), SensorReading.metric.asc())
    )
    return [
        (str(metric), int(index), float(average), int(count), str(unit))
        for metric, index, average, count, unit in session.execute(query)
    ]


def _parse_bucket(value: str) -> int | None:
    normalized = value.strip().lower()
    if normalized == "auto":
        return None
    match = BUCKET_PATTERN.fullmatch(normalized)
    if match is None:
        raise InvalidClimateHistory(
            "bucket must be 'auto' or a duration such as 5m, 1h, or 1d"
        )
    multipliers = {"s": 1, "m": 60, "h": 3_600, "d": 86_400}
    seconds = int(match.group("amount")) * multipliers[match.group("unit")]
    if seconds > int(HARD_MAX_HISTORY_WINDOW.total_seconds()):
        raise InvalidClimateHistory("bucket exceeds the maximum history window")
    return seconds


def _nice_bucket(seconds: int) -> int:
    for candidate in NICE_BUCKET_SECONDS:
        if candidate >= seconds:
            return candidate
    return seconds


def _max_history_window(settings: Settings) -> timedelta:
    configured = _duration_seconds(settings.graphs.max_window)
    if configured is None or configured <= 0:
        return HARD_MAX_HISTORY_WINDOW
    return min(timedelta(seconds=configured), HARD_MAX_HISTORY_WINDOW)


def _duration_seconds(value: str) -> int | None:
    match = BUCKET_PATTERN.fullmatch(str(value).strip().lower())
    if match is None:
        return None
    multipliers = {"s": 1, "m": 60, "h": 3_600, "d": 86_400}
    return int(match.group("amount")) * multipliers[match.group("unit")]


def _limit_points(
    points: list[ClimateHistoryPoint],
    point_limit: int,
) -> list[ClimateHistoryPoint]:
    if len(points) <= point_limit:
        return points

    group_size = math.ceil(len(points) / point_limit)
    reduced: list[ClimateHistoryPoint] = []
    for group in _chunks(points, group_size):
        total_samples = sum(point.sample_count for point in group)
        weighted_value = sum(
            point.value * point.sample_count for point in group
        ) / total_samples
        reduced.append(
            ClimateHistoryPoint(
                timestamp=group[0].timestamp,
                value=weighted_value,
                sample_count=total_samples,
            )
        )
    return reduced


def _chunks(
    values: list[ClimateHistoryPoint],
    size: int,
) -> Iterable[list[ClimateHistoryPoint]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _default_metric_unit(settings: Settings, metric: str) -> str:
    if metric == "temperature":
        return settings.temperatures.default_unit
    return settings.temperatures.default_humidity_unit


def _is_stale(updated_at: datetime, now: datetime, stale_after_sec: int) -> bool:
    return (now - _as_utc(updated_at)).total_seconds() > stale_after_sec


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
