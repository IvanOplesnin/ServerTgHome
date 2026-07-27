from __future__ import annotations

from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from server_tg_home.core.config import TelegramPanelConfig

PANEL_CALLBACK_PREFIX = "sth:p"


def build_panel_text(panel_id: str, panel: TelegramPanelConfig) -> str:
    title = escape(panel.title)
    if panel.kind in {"door", "camera"}:
        return f"<b>{title}</b>\n\nБыстрые действия камеры."
    if panel.kind == "climate":
        return f"<b>{title}</b>\n\nТемпература, влажность и графики."
    return f"<b>{title}</b>"


def build_panel_markup(
    panel_id: str,
    panel: TelegramPanelConfig,
    *,
    mini_app_url: str | None = None,
) -> InlineKeyboardMarkup:
    if panel.kind in {"door", "camera"}:
        rows = [
            [
                InlineKeyboardButton(
                    text=f"Клип {panel.video_duration_sec} сек",
                    callback_data=_callback_data(panel_id, "clip"),
                ),
                InlineKeyboardButton(
                    text="Фото",
                    callback_data=_callback_data(panel_id, "snapshot"),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"Запись {panel.record_duration_sec // 60} мин",
                    callback_data=_callback_data(panel_id, "record"),
                ),
                InlineKeyboardButton(
                    text="Список видео",
                    callback_data=_callback_data(panel_id, "videos"),
                ),
            ],
        ]
        _append_mini_app_button(rows, mini_app_url)
        return InlineKeyboardMarkup(inline_keyboard=rows)
    if panel.kind == "climate":
        rows = [
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
        _append_mini_app_button(rows, mini_app_url)
        return InlineKeyboardMarkup(inline_keyboard=rows)
    raise ValueError(f"Unsupported panel kind: {panel.kind}")


def build_mini_app_markup(mini_app_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть умный дом",
                    url=mini_app_url,
                )
            ]
        ]
    )


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


def _append_mini_app_button(
    rows: list[list[InlineKeyboardButton]],
    mini_app_url: str | None,
) -> None:
    if mini_app_url:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Открыть умный дом",
                    url=mini_app_url,
                )
            ]
        )
