import type {
  BootstrapResponse,
  Camera,
  CamerasResponse,
  ClimateCurrentResponse,
  ClimateHistoryResponse,
  DownloadTicket,
  SessionResponse,
  StreamTicket,
  VideoRecording,
  VideosResponse,
} from "./types";

const API_ROOT = `${(import.meta.env.VITE_API_BASE_URL as string | undefined) ?? ""}/api/webapp/v1`;
let accessToken: string | undefined;

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers);
  let body: BodyInit | undefined;

  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.body);
  }
  if (accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }

  const response = await fetch(`${API_ROOT}${path}`, {
    ...options,
    body,
    credentials: "include",
    headers,
  });

  if (!response.ok) {
    let message = `Ошибка запроса (${response.status})`;
    try {
      const payload = (await response.json()) as {
        detail?: string | { message?: string };
        message?: string;
      };
      const detail =
        typeof payload.detail === "string" ? payload.detail : payload.detail?.message;
      message = detail ?? payload.message ?? message;
    } catch {
      // The generic message is safe for non-JSON proxy errors.
    }
    throw new ApiError(message, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function queryString(values: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  });
  const query = search.toString();
  return query ? `?${query}` : "";
}

export const api = {
  async createSession(initData: string, signal?: AbortSignal): Promise<SessionResponse> {
    const session = await request<SessionResponse>("/session", {
      method: "POST",
      body: { init_data: initData },
      signal,
    });
    // The fallback bearer token intentionally lives only in module memory. It is
    // never persisted to localStorage, IndexedDB, logs, or the URL.
    accessToken = session.access_token;
    return session;
  },

  async deleteSession(): Promise<void> {
    try {
      await request("/session", { method: "DELETE" });
    } finally {
      accessToken = undefined;
    }
  },

  getBootstrap(signal?: AbortSignal): Promise<BootstrapResponse> {
    return request("/bootstrap", { signal });
  },

  async getCameras(signal?: AbortSignal): Promise<Camera[]> {
    const payload = await request<Camera[] | CamerasResponse>("/cameras", { signal });
    if (Array.isArray(payload)) {
      return payload;
    }
    return payload.items ?? payload.cameras ?? [];
  },

  getVideos(
    cameraId: string,
    cursor?: string,
    limit = 12,
    signal?: AbortSignal,
  ): Promise<VideosResponse> {
    const query = queryString({
      camera_id: cameraId,
      cursor,
      limit,
    });
    return request(`/videos${query}`, { signal });
  },

  videoContentUrl(videoId: VideoRecording["id"]): string {
    return `${API_ROOT}/videos/${encodeURIComponent(String(videoId))}/content`;
  },

  createDownloadTicket(videoId: VideoRecording["id"]): Promise<DownloadTicket> {
    return request(`/videos/${encodeURIComponent(String(videoId))}/download-ticket`, {
      method: "POST",
    });
  },

  getClimateCurrent(signal?: AbortSignal): Promise<ClimateCurrentResponse> {
    return request("/climate/current", { signal });
  },

  getClimateHistory(
    roomId: string,
    from: string,
    to: string,
    signal?: AbortSignal,
  ): Promise<ClimateHistoryResponse> {
    const query = queryString({
      room_id: roomId,
      from,
      to,
      bucket: "auto",
      point_limit: 500,
    });
    return request(`/climate/history${query}`, { signal });
  },

  createStreamTicket(cameraId: string, signal?: AbortSignal): Promise<StreamTicket> {
    return request(`/streams/${encodeURIComponent(cameraId)}/ticket`, {
      method: "POST",
      signal,
    });
  },
};
