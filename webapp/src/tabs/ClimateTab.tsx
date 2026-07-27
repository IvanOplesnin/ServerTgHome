import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import type {
  ClimateCurrentResponse,
  ClimateHistoryResponse,
  ClimateMetricReading,
} from "../api/types";
import { ClimateChart } from "../components/ClimateChart";
import { RefreshIcon } from "../components/Icons";
import { EmptyState, ErrorState, SectionSkeleton } from "../components/States";
import { formatRelativeTime } from "../lib/format";
import type { TabComponentProps } from "./registryTypes";

interface RoomView {
  id: string;
  title: string;
  temperature?: ClimateMetricReading;
  humidity?: ClimateMetricReading;
}

const ranges = [
  { id: "24h", title: "24 часа", hours: 24 },
  { id: "7d", title: "7 дней", hours: 24 * 7 },
  { id: "30d", title: "30 дней", hours: 24 * 30 },
] as const;

function normalizeRooms(payload: ClimateCurrentResponse): RoomView[] {
  const readings = payload.rooms ?? payload.items ?? [];
  return readings.map((room) => ({
    id: room.room_id ?? room.id ?? "",
    title: room.room_title ?? room.title ?? room.room_id ?? room.id ?? "Комната",
    temperature:
      room.temperature ??
      legacyMetric(room.temperature_c, "°C", room.updated_at, room.stale),
    humidity:
      room.humidity ??
      legacyMetric(room.humidity_percent, "%", room.updated_at, room.stale),
  }));
}

function legacyMetric(
  value: number | null | undefined,
  unit: string,
  updatedAt?: string | null,
  stale?: boolean,
): ClimateMetricReading | undefined {
  if (value === null || value === undefined) {
    return undefined;
  }
  return {
    value,
    unit,
    updated_at: updatedAt,
    stale,
  };
}

function roomUpdatedAt(room: RoomView): string | undefined {
  const values = [room.temperature?.updated_at, room.humidity?.updated_at].filter(
    (value): value is string => Boolean(value),
  );
  return values.sort((left, right) => new Date(right).getTime() - new Date(left).getTime())[0];
}

export function ClimateTab({ bootstrap }: TabComponentProps): React.ReactElement {
  const [rooms, setRooms] = useState<RoomView[]>(() =>
    (bootstrap.climate_rooms ?? []).map((room) => ({
      id: room.id,
      title: room.title,
    })),
  );
  const [selectedRoomId, setSelectedRoomId] = useState(
    bootstrap.climate_rooms?.[0]?.id ?? "",
  );
  const [rangeId, setRangeId] = useState<(typeof ranges)[number]["id"]>("24h");
  const [history, setHistory] = useState<ClimateHistoryResponse>();
  const [currentLoading, setCurrentLoading] = useState(true);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [currentError, setCurrentError] = useState<string>();
  const [historyError, setHistoryError] = useState<string>();

  const selectedRoom = rooms.find((room) => room.id === selectedRoomId) ?? rooms[0];
  const selectedRange = ranges.find((range) => range.id === rangeId) ?? ranges[0];

  const loadCurrent = useCallback(async (signal?: AbortSignal) => {
    setCurrentError(undefined);
    try {
      const response = await api.getClimateCurrent(signal);
      const nextRooms = normalizeRooms(response);
      setRooms(nextRooms);
      setSelectedRoomId((current) =>
        nextRooms.some((room) => room.id === current) ? current : (nextRooms[0]?.id ?? ""),
      );
    } catch (error) {
      if (!signal?.aborted) {
        setCurrentError(error instanceof Error ? error.message : "Не удалось загрузить показания");
      }
    } finally {
      if (!signal?.aborted) {
        setCurrentLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadCurrent(controller.signal);
    const interval = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        void loadCurrent(controller.signal);
      }
    }, 60_000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [loadCurrent]);

  useEffect(() => {
    if (!selectedRoom) {
      setHistory(undefined);
      return;
    }
    const controller = new AbortController();
    const to = new Date();
    const from = new Date(to.getTime() - selectedRange.hours * 60 * 60 * 1000);
    setHistoryLoading(true);
    setHistoryError(undefined);
    api
      .getClimateHistory(
        selectedRoom.id,
        from.toISOString(),
        to.toISOString(),
        controller.signal,
      )
      .then(setHistory)
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setHistoryError(
            error instanceof Error ? error.message : "Не удалось загрузить историю",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setHistoryLoading(false);
        }
      });
    return () => controller.abort();
  }, [selectedRange.hours, selectedRoom]);

  const staleRooms = useMemo(
    () =>
      rooms.filter((room) => room.temperature?.stale || room.humidity?.stale).length,
    [rooms],
  );

  return (
    <main className="tab-page">
      <header className="page-heading">
        <div>
          <span className="page-heading__eyebrow">Датчики дома</span>
          <h1>Климат</h1>
        </div>
        <button
          aria-label="Обновить показания"
          className="icon-button"
          disabled={currentLoading}
          onClick={() => void loadCurrent()}
          type="button"
        >
          <RefreshIcon />
        </button>
      </header>

      {currentError && rooms.length === 0 ? (
        <ErrorState message={currentError} onRetry={() => void loadCurrent()} />
      ) : (
        <>
          {staleRooms > 0 && (
            <p className="climate-warning">
              У {staleRooms} {staleRooms === 1 ? "комнаты" : "комнат"} показания устарели
            </p>
          )}
          <div className="climate-cards">
            {rooms.map((room) => {
              const selected = room.id === selectedRoom?.id;
              const stale = room.temperature?.stale || room.humidity?.stale;
              return (
                <button
                  aria-pressed={selected}
                  className={`climate-card ${selected ? "climate-card--active" : ""}`}
                  key={room.id}
                  onClick={() => setSelectedRoomId(room.id)}
                  type="button"
                >
                  <span className="climate-card__topline">
                    <strong>{room.title}</strong>
                    <i className={stale ? "is-stale" : ""} />
                  </span>
                  <span className="climate-card__values">
                    <span>
                      <b>
                        {room.temperature ? room.temperature.value.toFixed(1) : "—"}
                      </b>
                      <small>{room.temperature?.unit ?? "°C"}</small>
                    </span>
                    <span className="climate-card__divider" />
                    <span>
                      <b>{room.humidity ? Math.round(room.humidity.value) : "—"}</b>
                      <small>{room.humidity?.unit ?? "%"}</small>
                    </span>
                  </span>
                  <span className="climate-card__updated">
                    {formatRelativeTime(roomUpdatedAt(room))}
                  </span>
                </button>
              );
            })}
          </div>
          {currentLoading && rooms.length === 0 && <SectionSkeleton rows={3} />}
          {!currentLoading && rooms.length === 0 && (
            <EmptyState
              message="Показания появятся после первого события от Home Assistant."
              title="Датчики не передали данные"
            />
          )}
          {currentError && rooms.length > 0 && (
            <p className="inline-error" role="alert">
              {currentError}
            </p>
          )}
        </>
      )}

      {selectedRoom && (
        <section className="section climate-history">
          <header className="section__header section__header--stack">
            <div>
              <span className="section__eyebrow">Динамика</span>
              <h2>{selectedRoom.title}</h2>
            </div>
            <div aria-label="Период графика" className="range-selector">
              {ranges.map((range) => (
                <button
                  aria-pressed={range.id === rangeId}
                  className={range.id === rangeId ? "is-active" : ""}
                  key={range.id}
                  onClick={() => setRangeId(range.id)}
                  type="button"
                >
                  {range.title}
                </button>
              ))}
            </div>
          </header>

          {historyLoading && !history ? (
            <div className="chart-loading" role="status">
              <span className="spinner" />
              Строим график…
            </div>
          ) : historyError ? (
            <ErrorState message={historyError} />
          ) : history ? (
            <ClimateChart history={history} rangeHours={selectedRange.hours} />
          ) : (
            <EmptyState message="История пока отсутствует." title="Нет данных" />
          )}
        </section>
      )}
    </main>
  );
}
