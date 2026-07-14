from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from server_tg_home.database.models import AppState, utcnow

ARMED_KEY = "notifications_armed"
MUTED_UNTIL_KEY = "notifications_muted_until"


def notifications_enabled(session: Session) -> tuple[bool, str | None]:
    if not notifications_armed(session):
        return False, "notifications are disarmed"
    muted_until = notifications_muted_until(session)
    if muted_until is not None and muted_until > datetime.now(UTC):
        return False, f"notifications are muted until {muted_until.isoformat()}"
    return True, None


def notifications_armed(session: Session) -> bool:
    value = _get_value(session, ARMED_KEY)
    if value is None:
        return True
    return bool(value.get("enabled", True))


def set_notifications_armed(session: Session, enabled: bool) -> None:
    _set_value(session, ARMED_KEY, {"enabled": enabled})


def notifications_muted_until(session: Session) -> datetime | None:
    value = _get_value(session, MUTED_UNTIL_KEY)
    if not value or not value.get("until"):
        return None
    parsed = datetime.fromisoformat(str(value["until"]))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def mute_notifications(session: Session, duration: timedelta) -> datetime:
    until = datetime.now(UTC) + duration
    _set_value(session, MUTED_UNTIL_KEY, {"until": until.isoformat()})
    return until


def clear_notification_mute(session: Session) -> None:
    _set_value(session, MUTED_UNTIL_KEY, {"until": None})


def runtime_state_text(session: Session) -> str:
    armed = notifications_armed(session)
    muted_until = notifications_muted_until(session)
    lines = ["Notifications"]
    lines.append(f"Armed: {'yes' if armed else 'no'}")
    if muted_until is None:
        lines.append("Muted: no")
    elif muted_until <= datetime.now(UTC):
        lines.append("Muted: expired")
    else:
        lines.append(f"Muted until: {muted_until.isoformat()}")
    return "\n".join(lines)


def _get_value(session: Session, key: str) -> dict[str, Any] | None:
    row = session.get(AppState, key)
    return dict(row.value) if row is not None else None


def _set_value(session: Session, key: str, value: dict[str, Any]) -> None:
    row = session.get(AppState, key)
    if row is None:
        session.add(AppState(key=key, value=value, updated_at=utcnow()))
        return
    row.value = value
    row.updated_at = utcnow()
