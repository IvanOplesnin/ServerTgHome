from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from server_tg_home.core.config import Settings
from server_tg_home.database.models import AppState, utcnow

TEMPERATURE_KEY_PREFIX = "temperature:"


@dataclass(frozen=True)
class TemperatureReading:
    room_id: str
    title: str
    temperature: float
    unit: str
    entity_id: str | None
    updated_at: datetime


@dataclass(frozen=True)
class TemperatureUpdateResult:
    updated: list[str]
    skipped: list[str]


def update_temperatures_from_payload(
    session: Session,
    settings: Settings,
    payload: dict[str, Any],
) -> TemperatureUpdateResult:
    updates = _extract_temperature_updates(payload)
    if not updates:
        raise ValueError("temperature webhook requires room and temperature values")

    updated: list[str] = []
    skipped: list[str] = []
    for item in updates:
        room_id = _room_id(item)
        if room_id not in settings.temperatures.rooms:
            raise ValueError(f"Unknown temperature room: {room_id}")
        temperature = _parse_temperature(_temperature_value(item))
        if temperature is None:
            skipped.append(room_id)
            continue
        unit = str(item.get("unit") or settings.temperatures.default_unit)
        entity_id = item.get("entity_id")
        update_temperature(
            session,
            room_id=room_id,
            temperature=temperature,
            unit=unit,
            entity_id=str(entity_id) if entity_id else None,
        )
        updated.append(room_id)
    if not updated:
        raise ValueError("temperature webhook does not contain valid temperature values")
    return TemperatureUpdateResult(updated=updated, skipped=skipped)


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


def build_temperature_text(settings: Settings, session: Session) -> str:
    lines = ["Температура"]
    for room_id, room in settings.temperatures.rooms.items():
        reading = get_temperature(session, settings, room_id)
        if reading is None:
            lines.append(f"{room.title}: нет данных")
            continue
        age = datetime.now(UTC) - reading.updated_at
        stale = age.total_seconds() > settings.temperatures.stale_after_sec
        suffix = f", {_format_age(age.total_seconds())} назад"
        if stale:
            suffix += ", устарело"
        lines.append(f"{reading.title}: {_format_temperature(reading.temperature)} {reading.unit}{suffix}")
    return "\n".join(lines)


def _extract_temperature_updates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if "temperatures" in payload:
        return _extract_batch(payload["temperatures"], payload)
    if "rooms" in payload:
        return _extract_batch(payload["rooms"], payload)
    return [payload]


def _extract_batch(value: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    unit = payload.get("unit")
    if isinstance(value, dict):
        updates = []
        for room_id, item in value.items():
            if isinstance(item, dict):
                update = dict(item)
                update.setdefault("room", room_id)
            else:
                update = {"room": room_id, "temperature": item}
            if unit is not None:
                update.setdefault("unit", unit)
            updates.append(update)
        return updates
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _room_id(item: dict[str, Any]) -> str:
    value = item.get("room") or item.get("room_id")
    if not value:
        raise ValueError("temperature update requires room")
    return str(value)


def _temperature_value(item: dict[str, Any]) -> Any:
    for key in ("temperature", "value", "state"):
        if key in item:
            return item[key]
    return None


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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
