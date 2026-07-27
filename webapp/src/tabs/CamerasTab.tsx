import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type {
  Camera,
  CameraHealthDetails,
  DownloadTicket,
  RecordingActivity,
  VideoRecording,
} from "../api/types";
import {
  CameraIcon,
  ChevronIcon,
  DownloadIcon,
  PlayIcon,
  RecordIcon,
  RefreshIcon,
  StopIcon,
} from "../components/Icons";
import { EmptyState, ErrorState, SectionSkeleton } from "../components/States";
import { formatBytes, formatDateTime, formatDuration, safeFilename } from "../lib/format";
import { classifyVideoPlaybackFailure } from "../lib/media";
import {
  cameraRecording,
  formatRecordingDuration,
  recordingBlocksStart,
  recordingButtonLabel,
  recordingDescription,
} from "../lib/recordings";
import { Go2RtcPlayer } from "../live/Go2RtcPlayer";
import { notify, requestTelegramDownload } from "../telegram";
import type { TabComponentProps } from "./registryTypes";

interface PlaybackErrorState {
  message: string;
  retryable: boolean;
}

interface RecordingFeedback {
  cameraId: string;
  kind: "success" | "error";
  message: string;
}

function cameraHealth(camera: Camera): {
  state: "online" | "offline" | "degraded" | "unknown";
  label: string;
  reason?: string;
} {
  const details =
    typeof camera.health === "object" ? (camera.health as CameraHealthDetails) : undefined;
  const rawState = details?.state ?? camera.health ?? camera.status;
  const normalized = String(rawState ?? "").toLowerCase();
  const available = details?.available ?? camera.online;

  if (available === false || ["offline", "unavailable", "down"].includes(normalized)) {
    return { state: "offline", label: "Не в сети", reason: details?.reason ?? undefined };
  }
  if (["degraded", "stale", "warning"].includes(normalized)) {
    return { state: "degraded", label: "Есть задержка", reason: details?.reason ?? undefined };
  }
  if (available === true || ["online", "ok", "healthy", "available"].includes(normalized)) {
    return { state: "online", label: "В сети", reason: details?.reason ?? undefined };
  }
  return { state: "unknown", label: "Статус неизвестен", reason: details?.reason ?? undefined };
}

function mergeCameras(original: Camera[], refreshed: Camera[]): Camera[] {
  const byId = new Map(refreshed.map((camera) => [camera.id, camera]));
  return original.map((camera) => ({ ...camera, ...byId.get(camera.id) })).concat(
    refreshed.filter((camera) => !original.some((item) => item.id === camera.id)),
  );
}

function browserDownload(url: string, filename: string): void {
  const link = document.createElement("a");
  link.href = new URL(url, window.location.origin).href;
  link.download = filename;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
}

function ticketIsUsable(ticket: DownloadTicket | undefined): ticket is DownloadTicket {
  if (!ticket) {
    return false;
  }
  if (!ticket.expires_at) {
    return true;
  }
  const expiresAt = new Date(ticket.expires_at).getTime();
  return Number.isFinite(expiresAt) && expiresAt > Date.now() + 10_000;
}

export function CamerasTab({ bootstrap }: TabComponentProps): React.ReactElement {
  const isAdmin = bootstrap.user.role === "admin" || bootstrap.user.is_admin === true;
  const [cameras, setCameras] = useState(bootstrap.cameras);
  const [selectedId, setSelectedId] = useState(bootstrap.cameras[0]?.id ?? "");
  const [liveCameraId, setLiveCameraId] = useState<string>();
  const [healthError, setHealthError] = useState<string>();
  const [recordings, setRecordings] = useState<RecordingActivity[]>([]);
  const [recordingsError, setRecordingsError] = useState<string>();
  const [recordingStartingIds, setRecordingStartingIds] = useState<Set<string>>(
    new Set(),
  );
  const [recordingFeedback, setRecordingFeedback] = useState<RecordingFeedback>();
  const [videos, setVideos] = useState<VideoRecording[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>();
  const [archiveLoading, setArchiveLoading] = useState(false);
  const [archiveError, setArchiveError] = useState<string>();
  const [expandedVideoId, setExpandedVideoId] = useState<string>();
  const [videoTickets, setVideoTickets] = useState<Record<string, DownloadTicket>>({});
  const [ticketLoadingId, setTicketLoadingId] = useState<string>();
  const [ticketErrors, setTicketErrors] = useState<Record<string, string>>({});
  const [playbackErrors, setPlaybackErrors] = useState<
    Record<string, PlaybackErrorState>
  >({});
  const [downloadingId, setDownloadingId] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const archiveRequestRef = useRef(0);
  const previousRecordingsRef = useRef<RecordingActivity[]>([]);
  const recordingsRequestRef = useRef(0);
  const recordingStartRef = useRef<Set<string>>(new Set());
  const playbackRetryRef = useRef<Record<string, number>>({});
  const playbackResumeRef = useRef<Record<string, number>>({});

  const selectedCamera =
    cameras.find((camera) => camera.id === selectedId) ?? cameras[0];
  const selectedCameraId = selectedCamera?.id;
  const liveCamera = cameras.find((camera) => camera.id === liveCameraId);
  const stopLive = useCallback(() => setLiveCameraId(undefined), []);

  const refreshCameras = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const refreshed = await api.getCameras(signal);
        setCameras((current) => mergeCameras(current, refreshed));
        setHealthError(undefined);
      } catch (error) {
        if (signal?.aborted) {
          return;
        }
        setHealthError(
          error instanceof Error ? error.message : "Не удалось обновить состояние камер",
        );
      }
    },
    [],
  );

  useEffect(() => {
    const controller = new AbortController();
    void refreshCameras(controller.signal);
    const interval = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        void refreshCameras(controller.signal);
      }
    }, 30_000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [refreshCameras]);

  const loadVideos = useCallback(
    async (cursor?: string, append = false, signal?: AbortSignal) => {
      if (!selectedCameraId) {
        return;
      }
      const requestId = ++archiveRequestRef.current;
      setArchiveLoading(true);
      setArchiveError(undefined);
      try {
        const response = await api.getVideos(selectedCameraId, cursor, 12, signal);
        if (signal?.aborted || requestId !== archiveRequestRef.current) {
          return;
        }
        setVideos((current) => (append ? current.concat(response.items) : response.items));
        setNextCursor(response.next_cursor ?? null);
      } catch (error) {
        if (signal?.aborted || requestId !== archiveRequestRef.current) {
          return;
        }
        setArchiveError(error instanceof Error ? error.message : "Не удалось загрузить архив");
      } finally {
        if (requestId === archiveRequestRef.current) {
          setArchiveLoading(false);
        }
      }
    },
    [selectedCameraId],
  );

  useEffect(() => {
    const controller = new AbortController();
    setVideos([]);
    setNextCursor(undefined);
    setExpandedVideoId(undefined);
    setVideoTickets({});
    setTicketErrors({});
    setPlaybackErrors({});
    playbackRetryRef.current = {};
    playbackResumeRef.current = {};
    void loadVideos(undefined, false, controller.signal);
    return () => {
      controller.abort();
      archiveRequestRef.current += 1;
    };
  }, [loadVideos]);

  const refreshRecordings = useCallback(
    async (signal?: AbortSignal) => {
      if (!isAdmin) {
        return;
      }
      const requestId = ++recordingsRequestRef.current;
      try {
        const response = await api.getActiveRecordings(signal);
        if (
          signal?.aborted ||
          requestId !== recordingsRequestRef.current
        ) {
          return;
        }
        const previous = previousRecordingsRef.current;
        const activeIds = new Set(response.items.map((item) => item.job_id));
        const completedForSelectedCamera = previous.filter(
          (item) =>
            item.camera_id === selectedCameraId && !activeIds.has(item.job_id),
        );
        const completedIds = new Set(
          completedForSelectedCamera.map((item) => item.job_id),
        );
        const terminalResult = response.recent_results.find((result) =>
          completedIds.has(result.job_id),
        );
        previousRecordingsRef.current = response.items;
        setRecordings(response.items);
        setRecordingsError(undefined);
        if (completedForSelectedCamera.length > 0) {
          void loadVideos();
        }
        if (terminalResult?.status === "done") {
          setRecordingFeedback({
            cameraId: terminalResult.camera_id,
            kind: "success",
            message: "Запись завершена и сохранена в архив.",
          });
          notify("success");
        } else if (terminalResult?.status === "failed") {
          setRecordingFeedback({
            cameraId: terminalResult.camera_id,
            kind: "error",
            message:
              "Не удалось записать видео. Проверьте камеру и повторите.",
          });
          notify("error");
        }
      } catch (error) {
        if (
          signal?.aborted ||
          requestId !== recordingsRequestRef.current
        ) {
          return;
        }
        setRecordingsError(
          error instanceof Error
            ? error.message
            : "Не удалось обновить состояние записей",
        );
      }
    },
    [isAdmin, loadVideos, selectedCameraId],
  );

  useEffect(() => {
    if (!isAdmin) {
      recordingsRequestRef.current += 1;
      previousRecordingsRef.current = [];
      setRecordings([]);
      setRecordingsError(undefined);
      return;
    }
    const controller = new AbortController();
    void refreshRecordings(controller.signal);
    const interval = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        void refreshRecordings(controller.signal);
      }
    }, 10_000);
    return () => {
      recordingsRequestRef.current += 1;
      controller.abort();
      window.clearInterval(interval);
    };
  }, [isAdmin, refreshRecordings]);

  const startRecording = async (): Promise<void> => {
    if (
      !isAdmin ||
      !selectedCameraId ||
      recordingStartRef.current.has(selectedCameraId)
    ) {
      return;
    }
    const cameraId = selectedCameraId;
    const cameraTitle = selectedCamera?.title ?? cameraId;
    recordingStartRef.current.add(cameraId);
    setRecordingStartingIds((current) => new Set(current).add(cameraId));
    setRecordingFeedback(undefined);
    try {
      const response = await api.startRecording(cameraId);
      const optimistic: RecordingActivity = {
        job_id: response.job_id,
        camera_id: response.camera_id,
        status: response.status,
        phase: response.phase,
        duration_sec: response.duration_sec,
        created_at: response.created_at,
        started_at: null,
        expected_finish_at: null,
      };
      const nextRecordings = [
        ...previousRecordingsRef.current.filter(
          (item) => item.job_id !== optimistic.job_id,
        ),
        optimistic,
      ];
      previousRecordingsRef.current = nextRecordings;
      setRecordings(nextRecordings);
      setRecordingFeedback({
        cameraId,
        kind: "success",
        message: `${cameraTitle}: запись поставлена в очередь на ${formatRecordingDuration(
          response.duration_sec,
        )}.`,
      });
      notify("success");
      void refreshRecordings();
    } catch (error) {
      setRecordingFeedback({
        cameraId,
        kind: "error",
        message: `${cameraTitle}: ${
          error instanceof Error ? error.message : "не удалось запустить запись"
        }`,
      });
      notify("error");
    } finally {
      recordingStartRef.current.delete(cameraId);
      setRecordingStartingIds((current) => {
        const next = new Set(current);
        next.delete(cameraId);
        return next;
      });
    }
  };

  const startLive = (): void => {
    if (!selectedCamera) {
      return;
    }
    setLiveCameraId(selectedCamera.id);
    notify("success");
  };

  const getVideoTicket = async (
    video: VideoRecording,
    forceRefresh = false,
  ): Promise<DownloadTicket> => {
    const videoId = String(video.id);
    const cached = videoTickets[videoId];
    if (!forceRefresh && ticketIsUsable(cached)) {
      return cached;
    }
    setTicketLoadingId(videoId);
    setTicketErrors((current) => {
      const next = { ...current };
      delete next[videoId];
      return next;
    });
    try {
      const ticket = await api.createDownloadTicket(video.id);
      setVideoTickets((current) => ({ ...current, [videoId]: ticket }));
      setPlaybackErrors((current) => {
        const next = { ...current };
        delete next[videoId];
        return next;
      });
      return ticket;
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Не удалось получить ссылку на видео";
      setTicketErrors((current) => ({ ...current, [videoId]: message }));
      throw error;
    } finally {
      setTicketLoadingId((current) => (current === videoId ? undefined : current));
    }
  };

  const toggleVideo = (video: VideoRecording): void => {
    const videoId = String(video.id);
    if (expandedVideoId === videoId) {
      setExpandedVideoId(undefined);
      delete playbackRetryRef.current[videoId];
      delete playbackResumeRef.current[videoId];
      setPlaybackErrors((current) => {
        const next = { ...current };
        delete next[videoId];
        return next;
      });
      return;
    }
    setExpandedVideoId(videoId);
    if (!ticketIsUsable(videoTickets[videoId])) {
      void getVideoTicket(video).catch(() => undefined);
    }
  };

  const renewVideoPlayback = (
    video: VideoRecording,
    element: HTMLVideoElement,
  ): void => {
    const videoId = String(video.id);
    const retryCount = playbackRetryRef.current[videoId] ?? 0;
    if (retryCount >= 1) {
      setPlaybackErrors((current) => ({
        ...current,
        [videoId]: {
          message: "Не удалось продолжить воспроизведение после обновления ссылки.",
          retryable: true,
        },
      }));
      return;
    }

    playbackRetryRef.current[videoId] = retryCount + 1;
    if (Number.isFinite(element.currentTime) && element.currentTime > 0) {
      playbackResumeRef.current[videoId] = element.currentTime;
    }
    void getVideoTicket(video, true).catch(() => undefined);
  };

  const handleVideoPlaybackError = (
    video: VideoRecording,
    element: HTMLVideoElement,
  ): void => {
    const videoId = String(video.id);
    const failure = classifyVideoPlaybackFailure(element.error?.code);
    if (failure.renewTicket) {
      renewVideoPlayback(video, element);
      return;
    }
    const message = failure.message;
    if (!message) {
      return;
    }
    setPlaybackErrors((current) => ({
      ...current,
      [videoId]: { message, retryable: failure.retryable },
    }));
  };

  const retryVideoPlayback = (video: VideoRecording): void => {
    const videoId = String(video.id);
    playbackRetryRef.current[videoId] = 0;
    delete playbackResumeRef.current[videoId];
    void getVideoTicket(video, true).catch(() => undefined);
  };

  const downloadVideo = async (video: VideoRecording): Promise<void> => {
    const videoId = String(video.id);
    setDownloadingId(videoId);
    setNotice(undefined);
    try {
      const ticket = await getVideoTicket(video);
      const url = ticket.url ?? ticket.download_url;
      if (!url) {
        throw new Error("Сервер не вернул ссылку для скачивания");
      }
      const fallbackName = `${video.camera_id}-${new Date(video.created_at)
        .toISOString()
        .replace(/[:.]/g, "-")}.mp4`;
      const safeName = safeFilename(ticket.filename ?? video.filename, fallbackName);
      const absoluteUrl = new URL(url, window.location.origin).href;
      const fallback = () => browserDownload(absoluteUrl, safeName);
      if (!requestTelegramDownload(absoluteUrl, safeName, fallback)) {
        fallback();
      }
      setNotice("Скачивание запущено");
      notify("success");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось скачать видео");
      notify("error");
    } finally {
      setDownloadingId(undefined);
    }
  };

  const health = selectedCamera ? cameraHealth(selectedCamera) : undefined;
  const selectedRecording = selectedCameraId
    ? cameraRecording(recordings, selectedCameraId)
    : undefined;
  const recordingStarting = selectedCameraId
    ? recordingStartingIds.has(selectedCameraId)
    : false;
  const selectedRecordingDescription = recordingDescription(selectedRecording);
  const hasMore = Boolean(nextCursor);

  if (!selectedCamera) {
    return (
      <main className="tab-page">
        <PageHeading />
        <EmptyState
          message="Разрешённые камеры появятся здесь после настройки сервера."
          title="Камеры не настроены"
        />
      </main>
    );
  }

  return (
    <main className="tab-page">
      <PageHeading />

      <div aria-label="Выбор камеры" className="camera-selector" role="list">
        {cameras.map((camera) => {
          const itemHealth = cameraHealth(camera);
          return (
            <button
              aria-pressed={camera.id === selectedCamera.id}
              className={`camera-chip ${
                camera.id === selectedCamera.id ? "camera-chip--active" : ""
              }`}
              key={camera.id}
              onClick={() => {
                setSelectedId(camera.id);
                stopLive();
                setRecordingFeedback(undefined);
              }}
              role="listitem"
              type="button"
            >
              <span className={`status-dot status-dot--${itemHealth.state}`} />
              {camera.title}
            </button>
          );
        })}
      </div>

      <section className="camera-hero">
        <div className="camera-hero__visual">
          <div className="camera-hero__halo" />
          <CameraIcon />
        </div>
        <div className="camera-hero__content">
          <span className={`health-label health-label--${health?.state}`}>
            <i />
            {health?.label}
          </span>
          <h2>{selectedCamera.title}</h2>
          <p>{health?.reason ?? "Прямая трансляция и сохранённые записи"}</p>
        </div>
        <div className="camera-actions">
          <button
            className={`live-button ${liveCameraId === selectedCamera.id ? "live-button--stop" : ""}`}
            disabled={selectedCamera.live_available === false}
            onClick={liveCameraId === selectedCamera.id ? stopLive : startLive}
            type="button"
          >
            {liveCameraId === selectedCamera.id ? <StopIcon /> : <PlayIcon />}
            {liveCameraId === selectedCamera.id ? "Остановить" : "Смотреть"}
          </button>
          {isAdmin && (
            <button
              className={`record-button ${
                selectedRecording ? "record-button--active" : ""
              }`}
              aria-busy={recordingStarting}
              aria-label={`${recordingButtonLabel(
                selectedRecording,
                recordingStarting,
              )}: ${selectedCamera.title}`}
              disabled={
                recordingStarting || recordingBlocksStart(selectedRecording)
              }
              onClick={() => void startRecording()}
              type="button"
            >
              <RecordIcon />
              {recordingButtonLabel(selectedRecording, recordingStarting)}
            </button>
          )}
        </div>
        {isAdmin && selectedRecordingDescription && (
          <p className="recording-state">
            {selectedRecordingDescription}
          </p>
        )}
      </section>

      {recordingFeedback?.cameraId === selectedCameraId && (
        <p
          className={recordingFeedback.kind === "error" ? "inline-error" : "notice"}
          role={recordingFeedback.kind === "error" ? "alert" : "status"}
        >
          {recordingFeedback.message}
        </p>
      )}

      {isAdmin && recordingsError && (
        <div className="inline-warning">
          <span>{recordingsError}</span>
          <button onClick={() => void refreshRecordings()} type="button">
            Обновить
          </button>
        </div>
      )}

      {healthError && (
        <div className="inline-warning">
          <span>{healthError}</span>
          <button onClick={() => void refreshCameras()} type="button">
            Обновить
          </button>
        </div>
      )}

      {liveCamera && (
        <Go2RtcPlayer
          cameraId={liveCamera.id}
          cameraTitle={liveCamera.title}
          onStop={stopLive}
        />
      )}

      <section className="section">
        <header className="section__header">
          <div>
            <span className="section__eyebrow">SSD архив</span>
            <h2>Сохранённые видео</h2>
          </div>
          <button
            aria-label="Обновить список видео"
            className="icon-button"
            disabled={archiveLoading}
            onClick={() => void loadVideos()}
            type="button"
          >
            <RefreshIcon />
          </button>
        </header>

        {notice && (
          <p aria-live="polite" className="notice">
            {notice}
          </p>
        )}

        {archiveError && videos.length === 0 ? (
          <ErrorState message={archiveError} onRetry={() => void loadVideos()} />
        ) : (
          <>
            <div className="video-list">
              {videos.map((video) => {
                const videoId = String(video.id);
                const expanded = expandedVideoId === videoId;
                const ticket = videoTickets[videoId];
                const playbackError = playbackErrors[videoId];
                return (
                  <article className="video-card" key={videoId}>
                    <button
                      aria-expanded={expanded}
                      className="video-card__summary"
                      onClick={() => toggleVideo(video)}
                      type="button"
                    >
                      <span className="video-card__thumb">
                        {expanded ? <StopIcon /> : <PlayIcon />}
                      </span>
                      <span className="video-card__details">
                        <strong>{formatDateTime(video.created_at)}</strong>
                        <small>
                          {formatDuration(video.duration_sec)}
                          <i>•</i>
                          {formatBytes(video.size_bytes)}
                        </small>
                      </span>
                      <ChevronIcon className={expanded ? "rotate-90" : ""} />
                    </button>
                    {expanded && (
                      <div className="video-card__player">
                        {ticketLoadingId === videoId ? (
                          <div className="video-card__ticket-loading" role="status">
                            <span className="spinner" />
                            Готовим защищённый просмотр…
                          </div>
                        ) : ticketErrors[videoId] ? (
                          <ErrorState
                            message={ticketErrors[videoId]}
                            onRetry={() => void getVideoTicket(video, true)}
                            title="Видео недоступно"
                          />
                        ) : ticket?.content_url ? (
                          <>
                            {playbackError ? (
                              <ErrorState
                                message={playbackError.message}
                                onRetry={
                                  playbackError.retryable
                                    ? () => retryVideoPlayback(video)
                                    : undefined
                                }
                                title="Запись не воспроизводится"
                              />
                            ) : (
                              <video
                                controls
                                onError={(event) =>
                                  handleVideoPlaybackError(video, event.currentTarget)
                                }
                                onLoadedMetadata={(event) => {
                                  const resumeAt = playbackResumeRef.current[videoId];
                                  if (resumeAt && resumeAt < event.currentTarget.duration) {
                                    event.currentTarget.currentTime = resumeAt;
                                  }
                                }}
                                onPlaying={() => {
                                  playbackRetryRef.current[videoId] = 0;
                                  delete playbackResumeRef.current[videoId];
                                  setPlaybackErrors((current) => {
                                    const next = { ...current };
                                    delete next[videoId];
                                    return next;
                                  });
                                }}
                                playsInline
                                preload="metadata"
                                src={ticket.content_url}
                              >
                                Ваш браузер не поддерживает просмотр этого видео.
                              </video>
                            )}
                            <button
                              className="download-button"
                              disabled={downloadingId === videoId}
                              onClick={() => void downloadVideo(video)}
                              type="button"
                            >
                              <DownloadIcon />
                              {downloadingId === videoId
                                ? "Готовим ссылку…"
                                : "Скачать видео"}
                            </button>
                          </>
                        ) : (
                          <ErrorState
                            message="Сервер не вернул защищённую ссылку для просмотра."
                            onRetry={() => void getVideoTicket(video, true)}
                            title="Видео недоступно"
                          />
                        )}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>

            {archiveLoading && <SectionSkeleton rows={videos.length > 0 ? 1 : 3} />}
            {!archiveLoading && videos.length === 0 && (
              <EmptyState
                message="Для этой камеры пока нет доступных файлов."
                title="Архив пуст"
              />
            )}
            {archiveError && videos.length > 0 && (
              <p className="inline-error" role="alert">
                {archiveError}
              </p>
            )}
            {hasMore && (
              <button
                className="load-more-button"
                disabled={archiveLoading}
                onClick={() => void loadVideos(nextCursor ?? undefined, true)}
                type="button"
              >
                Показать ещё
              </button>
            )}
          </>
        )}
      </section>
    </main>
  );
}

function PageHeading(): React.ReactElement {
  return (
    <header className="page-heading">
      <div>
        <span className="page-heading__eyebrow">Умный дом</span>
        <h1>Камеры</h1>
      </div>
      <div aria-label="Защищённое подключение" className="secure-mark" title="Защищено">
        <span>●</span>
        Online
      </div>
    </header>
  );
}
