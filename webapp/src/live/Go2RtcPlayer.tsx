import { useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { StreamTicket } from "../api/types";
import { ErrorState } from "../components/States";

interface Go2RtcPlayerProps {
  cameraId: string;
  cameraTitle: string;
  onStop: () => void;
}

const loadedScripts = new Map<string, Promise<void>>();
const MAX_TICKET_REFRESH_DELAY_MS = 9 * 60 * 1000;
const MIN_TICKET_REFRESH_DELAY_MS = 5 * 1000;
const TICKET_REFRESH_MARGIN_MS = 30 * 1000;

function normalizedPlayerScript(ticket: StreamTicket): string {
  const candidate = ticket.player_script_url ?? "/media/video-stream.js";
  const resolved = new URL(candidate, window.location.origin);
  if (resolved.origin !== window.location.origin) {
    throw new Error("Медиаплеер должен загружаться с домена приложения");
  }
  return resolved.href;
}

async function loadPlayerScript(url: string): Promise<void> {
  const existing = loadedScripts.get(url);
  if (existing) {
    return existing;
  }

  const loading = import(/* @vite-ignore */ url).then(() => undefined);
  loadedScripts.set(url, loading);
  try {
    await loading;
  } catch (error) {
    loadedScripts.delete(url);
    throw error;
  }
}

function streamUrl(ticket: StreamTicket): string | undefined {
  return ticket.ws_url ?? ticket.url;
}

function ticketRefreshDelay(ticket: StreamTicket): number {
  if (!ticket.expires_at) {
    return MAX_TICKET_REFRESH_DELAY_MS;
  }
  const expiresAt = new Date(ticket.expires_at).getTime();
  if (!Number.isFinite(expiresAt)) {
    return MAX_TICKET_REFRESH_DELAY_MS;
  }
  return Math.max(
    MIN_TICKET_REFRESH_DELAY_MS,
    Math.min(
      MAX_TICKET_REFRESH_DELAY_MS,
      expiresAt - Date.now() - TICKET_REFRESH_MARGIN_MS,
    ),
  );
}

function mountGo2RtcPlayer(container: HTMLDivElement, ticket: StreamTicket): () => void {
  const url = streamUrl(ticket);
  if (!url) {
    throw new Error("Сервер не вернул адрес прямой трансляции");
  }

  const player = document.createElement("video-stream") as HTMLElement & {
    src: string;
    mode: string;
    media: string;
    background: boolean;
    controls?: boolean;
    muted?: boolean;
  };
  player.className = "live-player__video";
  // video-stream does not observe attributes. Set its public properties after the
  // module has registered the custom element so its transport setters run.
  player.mode = ticket.modes?.join(",") || "webrtc,mse,hls";
  player.media = ticket.media || "video,audio";
  player.background = false;
  player.controls = true;
  player.muted = true;
  player.src = url;
  container.replaceChildren(player);

  return () => {
    player.src = "";
    player.remove();
    container.replaceChildren();
  };
}

function mountNativeFallback(container: HTMLDivElement, ticket: StreamTicket): (() => void) | undefined {
  if (!ticket.hls_url) {
    return undefined;
  }
  const video = document.createElement("video");
  video.className = "live-player__video";
  video.controls = true;
  video.autoplay = true;
  video.muted = true;
  video.playsInline = true;
  video.src = ticket.hls_url;
  container.replaceChildren(video);
  void video.play().catch(() => undefined);

  return () => {
    video.pause();
    video.removeAttribute("src");
    video.load();
    video.remove();
  };
}

export function Go2RtcPlayer({
  cameraId,
  cameraTitle,
  onStop,
}: Go2RtcPlayerProps): React.ReactElement {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string>();
  const [reloadKey, setReloadKey] = useState(0);
  const [status, setStatus] = useState<"connecting" | "playing">("connecting");

  useEffect(() => {
    const controller = new AbortController();
    let dispose: (() => void) | undefined;
    let refreshTimer: number | undefined;
    let disposed = false;

    async function connect(): Promise<void> {
      setError(undefined);
      setStatus("connecting");
      try {
        const ticket = await api.createStreamTicket(cameraId, controller.signal);
        if (disposed || !containerRef.current) {
          return;
        }
        refreshTimer = window.setTimeout(() => {
          if (!disposed) {
            setReloadKey((value) => value + 1);
          }
        }, ticketRefreshDelay(ticket));

        try {
          await loadPlayerScript(normalizedPlayerScript(ticket));
          if (disposed || !containerRef.current) {
            return;
          }
          dispose = mountGo2RtcPlayer(containerRef.current, ticket);
        } catch (playerError) {
          if (disposed || !containerRef.current) {
            return;
          }
          dispose = mountNativeFallback(containerRef.current, ticket);
          if (!dispose) {
            throw playerError;
          }
        }
        setStatus("playing");
      } catch (requestError) {
        if (disposed || controller.signal.aborted) {
          return;
        }
        setError(requestError instanceof Error ? requestError.message : "Не удалось открыть трансляцию");
      }
    }

    void connect();
    return () => {
      disposed = true;
      controller.abort();
      if (refreshTimer !== undefined) {
        window.clearTimeout(refreshTimer);
      }
      dispose?.();
    };
  }, [cameraId, reloadKey]);

  useEffect(() => {
    const stopWhenHidden = () => {
      if (document.visibilityState === "hidden") {
        onStop();
      }
    };
    window.addEventListener("pagehide", onStop);
    document.addEventListener("visibilitychange", stopWhenHidden);
    return () => {
      window.removeEventListener("pagehide", onStop);
      document.removeEventListener("visibilitychange", stopWhenHidden);
    };
  }, [onStop]);

  return (
    <section aria-label={`Прямая трансляция: ${cameraTitle}`} className="live-player">
      <div className="live-player__header">
        <div>
          <span className="live-player__eyebrow">
            <i aria-hidden="true" />
            Прямой эфир
          </span>
          <h2>{cameraTitle}</h2>
        </div>
        <button className="small-button small-button--danger" onClick={onStop} type="button">
          Остановить
        </button>
      </div>
      <div className="live-player__viewport">
        <div ref={containerRef} />
        {!error && status === "connecting" && (
          <div className="live-player__loading" role="status">
            <span className="spinner" />
            Подключаемся к камере…
          </div>
        )}
      </div>
      {error && (
        <ErrorState
          message={error}
          onRetry={() => setReloadKey((value) => value + 1)}
          title="Трансляция недоступна"
        />
      )}
      <p className="live-player__hint">Звук включается вручную в плеере</p>
    </section>
  );
}
