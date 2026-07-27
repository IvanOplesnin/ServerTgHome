import type { ClimateHistoryPoint, ClimateHistoryResponse } from "../api/types";
import { buildLinePath, paddedDomain } from "../lib/chart";

interface MetricPoint {
  timestamp: string;
  value: number;
}

interface NormalizedHistory {
  temperature: MetricPoint[];
  humidity: MetricPoint[];
}

function normalizeHistory(history: ClimateHistoryResponse): NormalizedHistory {
  if (history.series) {
    const metric = (name: string): MetricPoint[] =>
      history.series
        ?.find((item) => item.metric === name)
        ?.points.filter(
          (point) =>
            Number.isFinite(point.value) &&
            !Number.isNaN(new Date(point.timestamp).getTime()),
        )
        .map((point) => ({ timestamp: point.timestamp, value: point.value })) ?? [];
    return {
      temperature: metric("temperature"),
      humidity: metric("humidity"),
    };
  }

  const points = history.points ?? [];
  const extract = (
    property: keyof Pick<ClimateHistoryPoint, "temperature_c" | "humidity_percent">,
  ): MetricPoint[] =>
    points.flatMap((point) => {
      const value = point[property];
      return value !== null && value !== undefined && Number.isFinite(value)
        ? [{ timestamp: point.timestamp, value }]
        : [];
    });
  return {
    temperature: extract("temperature_c"),
    humidity: extract("humidity_percent"),
  };
}

function formatAxisTime(timestamp: number, rangeHours: number): string {
  return new Intl.DateTimeFormat("ru-RU", {
    day: rangeHours > 48 ? "2-digit" : undefined,
    month: rangeHours > 48 ? "short" : undefined,
    hour: rangeHours <= 48 ? "2-digit" : undefined,
    minute: rangeHours <= 48 ? "2-digit" : undefined,
  }).format(timestamp);
}

export function ClimateChart({
  history,
  rangeHours,
}: {
  history: ClimateHistoryResponse;
  rangeHours: number;
}): React.ReactElement {
  const normalized = normalizeHistory(history);
  const allTimestamps = [...normalized.temperature, ...normalized.humidity]
    .map((point) => new Date(point.timestamp).getTime())
    .filter(Number.isFinite);

  if (allTimestamps.length === 0) {
    return (
      <div className="chart-empty">
        <strong>Нет данных за период</strong>
        <span>График появится после новых показаний датчиков.</span>
      </div>
    );
  }

  const width = 640;
  const height = 270;
  const left = 42;
  const right = 42;
  const top = 24;
  const bottom = 38;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const start = Math.min(...allTimestamps);
  const end = Math.max(...allTimestamps);
  const timeSpan = Math.max(end - start, 1);
  const temperatureDomain = paddedDomain(
    normalized.temperature.map((point) => point.value),
    2,
  );
  const humidityDomain = paddedDomain(
    normalized.humidity.map((point) => point.value),
    10,
  );

  const x = (timestamp: string): number =>
    left + ((new Date(timestamp).getTime() - start) / timeSpan) * plotWidth;
  const y = (value: number, domain: [number, number]): number =>
    top + ((domain[1] - value) / (domain[1] - domain[0])) * plotHeight;

  const temperaturePath = buildLinePath(
    normalized.temperature.map((point) => ({
      x: x(point.timestamp),
      y: y(point.value, temperatureDomain),
    })),
  );
  const humidityPath = buildLinePath(
    normalized.humidity.map((point) => ({
      x: x(point.timestamp),
      y: y(point.value, humidityDomain),
    })),
  );
  const lastTemperature = normalized.temperature.at(-1)?.value;
  const lastHumidity = normalized.humidity.at(-1)?.value;

  return (
    <div className="climate-chart">
      <div className="chart-legend">
        <span>
          <i className="chart-legend__temperature" />
          Температура
          {lastTemperature !== undefined && <strong>{lastTemperature.toFixed(1)}°</strong>}
        </span>
        <span>
          <i className="chart-legend__humidity" />
          Влажность
          {lastHumidity !== undefined && <strong>{Math.round(lastHumidity)}%</strong>}
        </span>
      </div>
      <svg
        aria-label="График температуры и влажности"
        preserveAspectRatio="none"
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        <defs>
          <linearGradient id="temperature-area" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--chart-temperature)" stopOpacity=".2" />
            <stop offset="100%" stopColor="var(--chart-temperature)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
          const lineY = top + ratio * plotHeight;
          return (
            <line
              className="chart-grid"
              key={ratio}
              x1={left}
              x2={width - right}
              y1={lineY}
              y2={lineY}
            />
          );
        })}
        {temperaturePath && (
          <path
            className="chart-line chart-line--temperature"
            d={temperaturePath}
            vectorEffect="non-scaling-stroke"
          />
        )}
        {humidityPath && (
          <path
            className="chart-line chart-line--humidity"
            d={humidityPath}
            vectorEffect="non-scaling-stroke"
          />
        )}
        <text className="chart-label chart-label--left" x={left} y={height - 12}>
          {formatAxisTime(start, rangeHours)}
        </text>
        <text
          className="chart-label chart-label--right"
          x={width - right}
          y={height - 12}
        >
          {formatAxisTime(end, rangeHours)}
        </text>
        <text className="chart-scale" x={left - 7} y={top + 4}>
          {temperatureDomain[1].toFixed(0)}°
        </text>
        <text className="chart-scale" x={left - 7} y={top + plotHeight}>
          {temperatureDomain[0].toFixed(0)}°
        </text>
        <text className="chart-scale chart-scale--right" x={width - right + 7} y={top + 4}>
          {humidityDomain[1].toFixed(0)}%
        </text>
        <text
          className="chart-scale chart-scale--right"
          x={width - right + 7}
          y={top + plotHeight}
        >
          {humidityDomain[0].toFixed(0)}%
        </text>
      </svg>
    </div>
  );
}
