import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("recording API client", () => {
  it("starts a recording with encoded camera id and CSRF marker", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push([input, init]);
        return new Response(
          JSON.stringify({
            job_id: "job-1",
            camera_id: "entrance/main",
            duration_sec: 20,
            status: "queued",
            phase: "queued",
            created_at: "2026-07-27T12:00:00Z",
          }),
          {
            headers: { "Content-Type": "application/json" },
            status: 202,
          },
        );
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.startRecording("entrance/main");

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, options] = calls[0]!;
    expect(options).toBeDefined();
    if (!options) {
      throw new Error("fetch options are missing");
    }
    expect(url).toBe(
      "/api/webapp/v1/cameras/entrance%2Fmain/recordings",
    );
    expect(options.method).toBe("POST");
    expect(options.credentials).toBe("include");
    expect(options.body).toBe("{}");
    const headers = options.headers as Headers;
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-STH-WebApp")).toBe("1");
  });

  it("requests live recording state without browser caching", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push([input, init]);
        return new Response(
          JSON.stringify({
            items: [],
            recent_results: [],
            generated_at: "2026-07-27T12:00:00Z",
          }),
          {
            headers: { "Content-Type": "application/json" },
            status: 200,
          },
        );
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.getActiveRecordings();

    const [, options] = calls[0]!;
    expect(options).toBeDefined();
    if (!options) {
      throw new Error("fetch options are missing");
    }
    expect(options.cache).toBe("no-store");
    expect(options.credentials).toBe("include");
  });
});
