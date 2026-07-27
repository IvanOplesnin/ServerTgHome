import { describe, expect, it } from "vitest";

import type { RecordingActivity } from "../api/types";
import {
  cameraRecording,
  formatRecordingDuration,
  recordingBlocksStart,
  recordingButtonLabel,
  recordingDescription,
} from "./recordings";

const queued: RecordingActivity = {
  job_id: "queued-job",
  camera_id: "entrance",
  status: "queued",
  phase: "queued",
  duration_sec: 600,
  created_at: "2026-07-27T12:00:00Z",
};

const running: RecordingActivity = {
  ...queued,
  job_id: "running-job",
  status: "running",
  phase: "recording",
  started_at: "2026-07-27T12:00:00Z",
  expected_finish_at: "2026-07-27T12:01:30Z",
};

describe("recording helpers", () => {
  it("prefers a running job over a queued job for the same camera", () => {
    expect(cameraRecording([queued, running], "entrance")?.job_id).toBe(
      "running-job",
    );
    expect(cameraRecording([queued], "living")).toBeUndefined();
    expect(recordingBlocksStart(running)).toBe(true);
    expect(recordingBlocksStart({ ...running, phase: "stale" })).toBe(false);
  });

  it("maps request and activity states to button labels", () => {
    expect(recordingButtonLabel(undefined, false)).toBe("Начать запись");
    expect(recordingButtonLabel(undefined, true)).toBe("Запускаем…");
    expect(recordingButtonLabel(queued, false)).toBe("В очереди");
    expect(recordingButtonLabel(running, false)).toBe("Идёт запись");
    expect(recordingButtonLabel({ ...running, phase: "finalizing" }, false)).toBe(
      "Сохраняем…",
    );
    expect(recordingButtonLabel({ ...running, phase: "stale" }, false)).toBe(
      "Начать запись",
    );
    expect(formatRecordingDuration(600)).toBe("10 мин");
  });

  it("describes queue, remaining time and file finalization", () => {
    expect(recordingDescription(queued)).toBe("Запись на SSD ожидает запуска");
    expect(
      recordingDescription(running, Date.parse("2026-07-27T12:00:30Z")),
    ).toBe("Запись на SSD: осталось примерно 1 мин");
    expect(
      recordingDescription(running, Date.parse("2026-07-27T12:02:00Z")),
    ).toBe("Запись завершается");
    expect(
      recordingDescription({ ...running, phase: "finalizing" }),
    ).toBe("Завершаем и сохраняем файл");
    expect(recordingDescription({ ...running, phase: "stale" })).toContain(
      "можно запустить новое",
    );
  });
});
