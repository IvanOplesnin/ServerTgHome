from __future__ import annotations

from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from server_tg_home.core.config import TelegramPanelConfig

PANEL_CALLBACK_PREFIX = "sth:p"


def build_panel_text(panel_id: str, panel: TelegramPanelConfig) -> str:
    title = escape(panel.title)
    if panel.kind == "door":
        return f"<b>{title}</b>\n\nБыстрые действия камеры."
    if panel.kind == "climate":
        return f"<b>{title}</b>\n\nТемпература, влажность и графики."
    return f"<b>{title}</b>"


def build_panel_markup(panel_id: str, panel: TelegramPanelConfig) -> InlineKeyboardMarkup:
    if panel.kind == "door":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"Видео {panel.video_duration_sec} сек",
                        callback_data=_callback_data(panel_id, "clip"),
                    ),
                    InlineKeyboardButton(
                        text="Фото",
                        callback_data=_callback_data(panel_id, "snapshot"),
                    ),
                ],
            ]
        )
    if panel.kind == "climate":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Сейчас",
                        callback_data=_callback_data(panel_id, "current"),
                    ),
                ],
                [
                    InlineKeyboardButton(text="6ч", callback_data=_callback_data(panel_id, "graph_6h")),
                    InlineKeyboardButton(text="12ч", callback_data=_callback_data(panel_id, "graph_12h")),
                    InlineKeyboardButton(text="24ч", callback_data=_callback_data(panel_id, "graph_24h")),
                ],
                [
                    InlineKeyboardButton(text="7д", callback_data=_callback_data(panel_id, "graph_7d")),
                    InlineKeyboardButton(text="30д", callback_data=_callback_data(panel_id, "graph_30d")),
                ],
            ]
        )
    raise ValueError(f"Unsupported panel kind: {panel.kind}")


def parse_panel_callback(data: str) -> tuple[str, str] | None:
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "sth" or parts[1] != "p":
        return None
    panel_id = parts[2]
    action = parts[3]
    if not panel_id or not action:
        return None
    return panel_id, action


def _callback_data(panel_id: str, action: str) -> str:
    return f"{PANEL_CALLBACK_PREFIX}:{panel_id}:{action}"
