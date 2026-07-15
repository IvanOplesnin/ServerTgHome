from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from server_tg_home.core.config import Settings
from server_tg_home.database.models import AppState, utcnow

TEMPERATURE_KEY_PREFIX = "temperature:"
HUMIDITY_KEY_PREFIX = "humidity:"


@dataclass(frozen=True)
class TemperatureReading:
    room_id: str
    title: str
    temperature: float
    unit: str
    entity_id: str | None
    updated_at: datetime


@dataclass(frozen=True)
class HumidityReading:
    room_id: str
    title: str
    humidity: float
    unit: str
    entity_id: str | None
    updated_at: datetime


@dataclass(frozen=True)
class TemperatureUpdateResult:
    updated: list[str]
    skipped: list[str]
    updated_humidity: list[str]
    skipped_humidity: list[str]


def update_temperatures_from_payload(
    session: Session,
    settings: Settings,
    payload: dict[str, Any],
    *,
    default_metric: str = "temperature",
) -> TemperatureUpdateResult:
    temperature_updates = _extract_metric_updates(payload, metric="temperature", default_metric=default_metric)
    humidity_updates = _extract_metric_updates(payload, metric="humidity", default_metric=default_metric)
    if not temperature_updates and not humidity_updates:
        raise ValueError("webhook requires room sensor values")

    updated: list[str] = []
    skipped: list[str] = []
    updated_humidity: list[str] = []
    skipped_humidity: list[str] = []

    for item in temperature_updates:
        room_id = _room_id(item)
        if room_id not in settings.temperatures.rooms:
            raise ValueError(f"Unknown temperature room: {room_id}")
        temperature = _parse_temperature(_temperature_value(item))
        if temperature is None:
            skipped.append(room_id)
            continue
        unit = _metric_unit(item, payload, settings, metric="temperature", default_metric=default_metric)
        entity_id = item.get("temperature_entity_id") or item.get("entity_id")
        update_temperature(
            session,
            room_id=room_id,
            temperature=temperature,
            unit=unit,
            entity_id=str(entity_id) if entity_id else None,
        )
        updated.append(room_id)

    for item in humidity_updates:
        room_id = _room_id(item)
        if room_id not in settings.temperatures.rooms:
            raise ValueError(f"Unknown humidity room: {room_id}")
        humidity = _parse_temperature(_humidity_value(item))
        if humidity is None:
            skipped_humidity.append(room_id)
            continue
        unit = _metric_unit(item, payload, settings, metric="humidity", default_metric=default_metric)
        entity_id = item.get("humidity_entity_id") or item.get("entity_id")
        update_humidity(
            session,
            room_id=room_id,
            humidity=humidity,
            unit=unit,
            entity_id=str(entity_id) if entity_id else None,
        )
        updated_humidity.append(room_id)

    if not updated and not updated_humidity:
        raise ValueError("webhook does not contain valid room sensor values")
    return TemperatureUpdateResult(
        updated=updated,
        skipped=skipped,
        updated_humidity=updated_humidity,
        skipped_humidity=skipped_humidity,
    )


def update_temperature(
    session: Session,
    room_id: str,
    temperature: float,
    unit: str,
    entity_id: str | None = None,
) -> None:
    now = utcnow()
    value = {
        "room_id": room_id,
        "temperature": temperature,
        "unit": unit,
        "entity_id": entity_id,
        "updated_at": now.isoformat(),
    }
    row = session.get(AppState, _temperature_key(room_id))
    if row is None:
        session.add(AppState(key=_temperature_key(room_id), value=value, updated_at=now))
        return
    row.value = value
    row.updated_at = now


def update_humidity(
    session: Session,
    room_id: str,
    humidity: float,
    unit: str,
    entity_id: str | None = None,
) -> None:
    now = utcnow()
    value = {
        "room_id": room_id,
        "humidity": humidity,
        "unit": unit,
        "entity_id": entity_id,
        "updated_at": now.isoformat(),
    }
    row = session.get(AppState, _humidity_key(room_id))
    if row is None:
        session.add(AppState(key=_humidity_key(room_id), value=value, updated_at=now))
        return
    row.value = value
    row.updated_at = now


def get_temperature(session: Session, settings: Settings, room_id: str) -> TemperatureReading | None:
    room = settings.temperatures.rooms.get(room_id)
    if room is None:
        return None
    row = session.get(AppState, _temperature_key(room_id))
    if row is None:
        return None
    value = dict(row.value)
    temperature = _parse_temperature(value.get("temperature"))
    if temperature is None:
        return None
    unit = str(value.get("unit") or settings.temperatures.default_unit)
    entity_id = value.get("entity_id")
    return TemperatureReading(
        room_id=room_id,
        title=room.title,
        temperature=temperature,
        unit=unit,
        entity_id=str(entity_id) if entity_id else None,
        updated_at=_as_utc(row.updated_at),
    )


def get_humidity(session: Session, settings: Settings, room_id: str) -> HumidityReading | None:
    room = settings.temperatures.rooms.get(room_id)
    if room is None:
        return None
    row = session.get(AppState, _humidity_key(room_id))
    if row is None:
        return None
    value = dict(row.value)
    humidity = _parse_temperature(value.get("humidity"))
    if humidity is None:
        return None
    unit = str(value.get("unit") or settings.temperatures.default_humidity_unit)
    entity_id = value.get("entity_id")
    return HumidityReading(
        room_id=room_id,
        title=room.title,
        humidity=humidity,
        unit=unit,
        entity_id=str(entity_id) if entity_id else None,
        updated_at=_as_utc(row.updated_at),
    )


def build_temperature_text(settings: Settings, session: Session) -> str:
    lines = ["Температура и влажность"]
    for room_id, room in settings.temperatures.rooms.items():
        temperature = get_temperature(session, settings, room_id)
        humidity = get_humidity(session, settings, room_id)
        lines.append(
            f"{room.title}: "
            f"{_format_reading('температура', temperature, settings.temperatures.stale_after_sec)}, "
            f"{_format_reading('влажность', humidity, settings.temperatures.stale_after_sec)}"
        )
    return "\n".join(lines)


def build_humidity_text(settings: Settings, session: Session) -> str:
    lines = ["Влажность"]
    for room_id, room in settings.temperatures.rooms.items():
        humidity = get_humidity(session, settings, room_id)
        lines.append(f"{room.title}: {_format_reading('влажность', humidity, settings.temperatures.stale_after_sec)}")
    return "\n".join(lines)


def _extract_metric_updates(payload: dict[str, Any], *, metric: str, default_metric: str) -> list[dict[str, Any]]:
    singular = metric
    plural = "temperatures" if metric == "temperature" else "humidities"
    if plural in payload:
        return _extract_batch(payload[plural], singular, include_scalars=True)
    if singular in payload:
        value = payload[singular]
        if isinstance(value, dict) and not (payload.get("room") or payload.get("room_id")):
            return _extract_batch(value, singular, include_scalars=True)
        return [payload]
    if "rooms" in payload:
        include_scalars = default_metric == metric
        return _extract_batch(payload["rooms"], singular, include_scalars=include_scalars)
    if default_metric == metric and (payload.get("room") or payload.get("room_id")):
        if "value" in payload or "state" in payload:
            return [payload]
    return []


def _extract_batch(value: Any, metric: str, *, include_scalars: bool) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        updates = []
        for room_id, item in value.items():
            if isinstance(item, dict):
                update = dict(item)
                update.setdefault("room", room_id)
            else:
                if not include_scalars:
                    continue
                update = {"room": room_id, metric: item}
            updates.append(update)
        return updates
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _room_id(item: dict[str, Any]) -> str:
    value = item.get("room") or item.get("room_id")
    if not value:
        raise ValueError("room sensor update requires room")
    return str(value)


def _temperature_value(item: dict[str, Any]) -> Any:
    for key in ("temperature", "value", "state"):
        if key in item:
            return item[key]
    return None


def _humidity_value(item: dict[str, Any]) -> Any:
    for key in ("humidity", "value", "state"):
        if key in item:
            return item[key]
    return None


def _metric_unit(
    item: dict[str, Any],
    payload: dict[str, Any],
    settings: Settings,
    *,
    metric: str,
    default_metric: str,
) -> str:
    if metric == "temperature":
        return str(
            item.get("temperature_unit")
            or item.get("unit")
            or payload.get("temperature_unit")
            or payload.get("unit")
            or settings.temperatures.default_unit
        )
    return str(
        item.get("humidity_unit")
        or payload.get("humidity_unit")
        or item.get("unit")
        or (payload.get("unit") if default_metric == "humidity" else None)
        or settings.temperatures.default_humidity_unit
    )


def _parse_temperature(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip().lower()
    if text in {"", "none", "null", "unknown", "unavailable"}:
        return None
    match = re.search(r"-?\d+(?:[\.,]\d+)?", text)
    if match is None:
        return None
    return float(match.group(0).replace(",", "."))


def _temperature_key(room_id: str) -> str:
    return f"{TEMPERATURE_KEY_PREFIX}{room_id}"


def _humidity_key(room_id: str) -> str:
    return f"{HUMIDITY_KEY_PREFIX}{room_id}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_reading(
    label: str,
    reading: TemperatureReading | HumidityReading | None,
    stale_after_sec: int,
) -> str:
    if reading is None:
        return f"{label}: нет данных"
    value = reading.temperature if isinstance(reading, TemperatureReading) else reading.humidity
    age = datetime.now(UTC) - reading.updated_at
    suffix = f"{_format_age(age.total_seconds())} назад"
    if age.total_seconds() > stale_after_sec:
        suffix += ", устарело"
    return f"{label} {_format_temperature(value)} {reading.unit} ({suffix})"


def _format_temperature(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}"


def _format_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} сек"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} ч"
    days = hours // 24
    return f"{days} д"
