from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean

import plotly.graph_objects as go
from plotly.subplots import make_subplots
from sqlalchemy import select
from sqlalchemy.orm import Session

from server_tg_home.core.config import Settings
from server_tg_home.database.models import SensorReading

METRIC_TITLES = {
    "temperature": "Температура",
    "humidity": "Влажность",
}

METRIC_UNITS = {
    "temperature": "°C",
    "humidity": "%",
}

ROOM_COLORS = [
    "#2563eb",
    "#dc2626",
    "#059669",
    "#7c3aed",
    "#ea580c",
    "#0891b2",
]


@dataclass(frozen=True)
class GraphRenderResult:
    png_path: Path
    html_path: Path
    caption: str


def render_sensor_graph(
    settings: Settings,
    session: Session,
    *,
    job_id: str,
    room_id: str,
    metrics: list[str],
    window_sec: int,
) -> GraphRenderResult:
    end = datetime.now(UTC)
    start = end - timedelta(seconds=window_sec)
    room_ids = list(settings.temperatures.rooms.keys()) if room_id == "all" else [room_id]

    readings = session.execute(
        select(SensorReading)
        .where(
            SensorReading.room_id.in_(room_ids),
            SensorReading.metric.in_(metrics),
            SensorReading.recorded_at >= start,
            SensorReading.recorded_at <= end,
        )
        .order_by(SensorReading.recorded_at.asc())
    ).scalars().all()

    grouped = _group_readings(readings)
    figure = _build_figure(settings, room_ids, metrics, grouped, start, end)
    png_path, html_path = _graph_paths(settings, job_id)
    png_path.parent.mkdir(parents=True, exist_ok=True)

    height = 150 + settings.graphs.height_per_panel * max(len(metrics), 1)
    figure.write_image(
        str(png_path),
        width=settings.graphs.width,
        height=height,
        scale=settings.graphs.scale,
    )
    figure.write_html(str(html_path), include_plotlyjs=True, full_html=True)

    caption = _build_caption(settings, room_ids, metrics, grouped, window_sec)
    return GraphRenderResult(png_path=png_path, html_path=html_path, caption=caption)


def _build_figure(
    settings: Settings,
    room_ids: list[str],
    metrics: list[str],
    grouped: dict[tuple[str, str], list[SensorReading]],
    start: datetime,
    end: datetime,
) -> go.Figure:
    subplot_titles = [METRIC_TITLES.get(metric, metric) for metric in metrics]
    figure = make_subplots(
        rows=len(metrics),
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.12,
        subplot_titles=subplot_titles,
    )

    for metric_index, metric in enumerate(metrics, start=1):
        has_metric_data = False
        for room_index, room_id in enumerate(room_ids):
            rows = grouped.get((room_id, metric), [])
            if not rows:
                continue
            has_metric_data = True
            room = settings.temperatures.rooms[room_id]
            color = ROOM_COLORS[room_index % len(ROOM_COLORS)]
            figure.add_trace(
                go.Scatter(
                    x=[_as_utc(row.recorded_at) for row in rows],
                    y=[row.value for row in rows],
                    mode="lines+markers",
                    line={"width": 3, "color": color},
                    marker={"size": 5, "color": color},
                    name=room.title,
                    hovertemplate="%{x|%d.%m %H:%M}<br>%{y:.1f} " + _metric_unit(metric, rows) + "<extra></extra>",
                ),
                row=metric_index,
                col=1,
            )

        if not has_metric_data:
            figure.add_annotation(
                text="Нет данных за выбранный период",
                xref="paper",
                yref="paper",
                x=0.5,
                y=1 - (metric_index - 0.5) / max(len(metrics), 1),
                showarrow=False,
                font={"color": "#64748b", "size": 16},
            )

        figure.update_yaxes(
            title_text=f"{METRIC_TITLES.get(metric, metric)}, {_metric_unit(metric)}",
            row=metric_index,
            col=1,
            gridcolor="#e2e8f0",
            zeroline=False,
        )

    room_title = "Все комнаты" if len(room_ids) > 1 else settings.temperatures.rooms[room_ids[0]].title
    figure.update_layout(
        title={
            "text": f"{room_title}: {_format_window_range(start, end)}",
            "x": 0.01,
            "xanchor": "left",
        },
        template="plotly_white",
        font={"family": "DejaVu Sans, Arial, sans-serif", "size": 14},
        hovermode="x unified",
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1,
        },
        margin={"l": 80, "r": 30, "t": 90, "b": 60},
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
    )
    figure.update_xaxes(range=[start, end], gridcolor="#e2e8f0")
    return figure


def _build_caption(
    settings: Settings,
    room_ids: list[str],
    metrics: list[str],
    grouped: dict[tuple[str, str], list[SensorReading]],
    window_sec: int,
) -> str:
    lines = [f"График за {_format_window(window_sec)}"]
    for room_id in room_ids:
        room = settings.temperatures.rooms[room_id]
        lines.append("")
        lines.append(room.title)
        for metric in metrics:
            rows = grouped.get((room_id, metric), [])
            if not rows:
                lines.append(f"{METRIC_TITLES.get(metric, metric)}: нет данных")
                continue
            values = [row.value for row in rows]
            unit = _metric_unit(metric, rows)
            lines.append(
                f"{METRIC_TITLES.get(metric, metric)}: "
                f"сейчас {_format_number(values[-1])} {unit}, "
                f"мин {_format_number(min(values))}, "
                f"сред {_format_number(mean(values))}, "
                f"макс {_format_number(max(values))}"
            )
    return "\n".join(lines)


def _group_readings(readings: list[SensorReading]) -> dict[tuple[str, str], list[SensorReading]]:
    grouped: dict[tuple[str, str], list[SensorReading]] = {}
    for reading in readings:
        grouped.setdefault((reading.room_id, reading.metric), []).append(reading)
    return grouped


def _graph_paths(settings: Settings, job_id: str) -> tuple[Path, Path]:
    now = datetime.now(UTC)
    directory = settings.graphs.path / now.strftime("%Y-%m-%d")
    filename = f"{now.strftime('%H%M%S')}_{job_id}"
    return directory / f"{filename}.png", directory / f"{filename}.html"


def _metric_unit(metric: str, rows: list[SensorReading] | None = None) -> str:
    if rows:
        return rows[-1].unit
    return METRIC_UNITS.get(metric, "")


def _format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}"


def _format_window(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400} д"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} ч"
    if seconds % 60 == 0:
        return f"{seconds // 60} мин"
    return f"{seconds} сек"


def _format_window_range(start: datetime, end: datetime) -> str:
    return f"{start.strftime('%d.%m %H:%M')} - {end.strftime('%d.%m %H:%M')}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
