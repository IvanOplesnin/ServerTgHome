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
const LIVE_START_TIMEOUT_MS = 45 * 1000;

interface MountedPlayer {
  dispose: () => void;
}

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

function supportsNativeHls(): boolean {
  const video = document.createElement("video");
  return Boolean(
    video.canPlayType("application/vnd.apple.mpegurl") ||
      video.canPlayType("application/x-mpegURL"),
  );
}

function liveMediaError(video: HTMLVideoElement): string {
  if (video.error?.code === 3 || video.error?.code === 4) {
    return "Встроенный плеер Telegram не поддерживает формат этого потока.";
  }
  return "Соединение с камерой прервано. Попробуйте подключиться ещё раз.";
}

function mountGo2RtcPlayer(
  container: HTMLDivElement,
  ticket: StreamTicket,
  onReady: () => void,
  onPlaying: () => void,
  onError: (message: string) => void,
): MountedPlayer {
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
    ondisconnect: () => void;
    visibilityCheck: boolean;
  };
  player.className = "live-player__video";
  // video-stream does not observe attributes. Set its public properties after the
  // module has registered the custom element so its transport setters run.
  player.mode = ticket.modes?.join(",") || "webrtc,mse,hls";
  player.media = ticket.media || "video,audio";
  player.background = false;
  player.visibilityCheck = false;
  player.controls = true;
  player.muted = true;
  player.src = url;
  container.replaceChildren(player);

  const video = player.querySelector("video");
  if (!(video instanceof HTMLVideoElement)) {
    player.remove();
    throw new Error("Медиаплеер не создал видеоэлемент");
  }
  video.autoplay = true;
  video.controls = true;
  video.muted = true;
  video.playsInline = true;
  const handleError = (): void => onError(liveMediaError(video));
  video.addEventListener("canplay", onReady);
  video.addEventListener("playing", onPlaying);
  video.addEventListener("error", handleError);
  void video.play().catch(() => undefined);

  return {
    dispose: () => {
      video.removeEventListener("canplay", onReady);
      video.removeEventListener("playing", onPlaying);
      video.removeEventListener("error", handleError);
      player.src = "";
      player.ondisconnect();
      player.remove();
      container.replaceChildren();
    },
  };
}

function mountNativeFallback(
  container: HTMLDivElement,
  ticket: StreamTicket,
  onReady: () => void,
  onPlaying: () => void,
  onError: (message: string) => void,
): MountedPlayer | undefined {
  if (!ticket.hls_url || !supportsNativeHls()) {
    return undefined;
  }
  const video = document.createElement("video");
  video.className = "live-player__video";
  video.controls = true;
  video.autoplay = true;
  video.muted = true;
  video.playsInline = true;
  const handleError = (): void => onError(liveMediaError(video));
  video.addEventListener("canplay", onReady);
  video.addEventListener("playing", onPlaying);
  video.addEventListener("error", handleError);
  video.src = ticket.hls_url;
  container.replaceChildren(video);
  void video.play().catch(() => undefined);

  return {
    dispose: () => {
      video.removeEventListener("canplay", onReady);
      video.removeEventListener("playing", onPlaying);
      video.removeEventListener("error", handleError);
      video.pause();
      video.removeAttribute("src");
      video.load();
      video.remove();
    },
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
  const [status, setStatus] = useState<"connecting" | "ready" | "playing">(
    "connecting",
  );

  useEffect(() => {
    const controller = new AbortController();
    let mounted: MountedPlayer | undefined;
    let refreshTimer: number | undefined;
    let startTimer: number | undefined;
    let activePlayer: "native-hls" | "go2rtc" | undefined;
    let switchingToGo2Rtc = false;
    let disposed = false;

    const clearStartTimer = (): void => {
      if (startTimer !== undefined) {
        window.clearTimeout(startTimer);
        startTimer = undefined;
      }
    };

    const markPlaying = (): void => {
      if (disposed) {
        return;
      }
      clearStartTimer();
      setError(undefined);
      setStatus("playing");
    };

    const markReady = (): void => {
      if (disposed) {
        return;
      }
      clearStartTimer();
      setError(undefined);
      setStatus((current) => (current === "playing" ? current : "ready"));
    };

    const showPlayerError = (message: string): void => {
      if (disposed) {
        return;
      }
      clearStartTimer();
      mounted?.dispose();
      mounted = undefined;
      setError(message);
    };

    const watchPlayerStart = (onTimeout: () => void): void => {
      clearStartTimer();
      startTimer = window.setTimeout(onTimeout, LIVE_START_TIMEOUT_MS);
    };

    const ensureGo2RtcRecoveryWatch = (): void => {
      if (startTimer !== undefined) {
        return;
      }
      startTimer = window.setTimeout(() => {
        showPlayerError(
          "Камера отвечает, но видео не запустилось. Проверьте формат потока или повторите подключение.",
        );
      }, LIVE_START_TIMEOUT_MS);
    };

    const handleGo2RtcError = (): void => {
      if (disposed) {
        return;
      }
      setError(undefined);
      setStatus("connecting");
      // video-stream has its own reconnect cycle. Keep it alive for one full
      // recovery window before presenting a terminal error.
      ensureGo2RtcRecoveryWatch();
    };

    const startGo2Rtc = async (
      ticket: StreamTicket,
      previousError?: string,
    ): Promise<void> => {
      if (disposed || switchingToGo2Rtc) {
        return;
      }
      switchingToGo2Rtc = true;
      clearStartTimer();
      mounted?.dispose();
      mounted = undefined;
      activePlayer = undefined;
      setError(undefined);
      setStatus("connecting");
      try {
        await loadPlayerScript(normalizedPlayerScript(ticket));
        if (disposed || !containerRef.current) {
          return;
        }
        mounted = mountGo2RtcPlayer(
          containerRef.current,
          ticket,
          markReady,
          markPlaying,
          handleGo2RtcError,
        );
        activePlayer = "go2rtc";
        watchPlayerStart(() => {
          showPlayerError(
            "Камера отвечает, но видео не запустилось. Проверьте формат потока или повторите подключение.",
          );
        });
      } catch (playerError) {
        if (disposed) {
          return;
        }
        const detail =
          playerError instanceof Error ? playerError.message : previousError;
        showPlayerError(
          detail || "Не удалось запустить защищённый плеер прямой трансляции.",
        );
      } finally {
        switchingToGo2Rtc = false;
      }
    };

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

        const fallbackToGo2Rtc = (message: string): void => {
          if (disposed || activePlayer !== "native-hls") {
            return;
          }
          void startGo2Rtc(ticket, message);
        };

        mounted = mountNativeFallback(
          containerRef.current,
          ticket,
          markReady,
          markPlaying,
          fallbackToGo2Rtc,
        );
        if (mounted) {
          activePlayer = "native-hls";
          watchPlayerStart(() => {
            fallbackToGo2Rtc(
              "HLS-поток не запустился во встроенном плеере Telegram.",
            );
          });
          return;
        }

        await startGo2Rtc(ticket);
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
      clearStartTimer();
      mounted?.dispose();
    };
  }, [cameraId, reloadKey]);

  useEffect(() => {
    window.addEventListener("pagehide", onStop);
    return () => {
      window.removeEventListener("pagehide", onStop);
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
      <p className="live-player__hint">
        {status === "ready"
          ? "Нажмите ▶ для запуска; звук включается вручную"
          : "Звук включается вручную в плеере"}
      </p>
    </section>
  );
}
