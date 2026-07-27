import type { RecordingActivity } from "../api/types";

export function cameraRecording(
  items: RecordingActivity[],
  cameraId: string,
): RecordingActivity | undefined {
  const priorities: RecordingActivity["phase"][] = [
    "recording",
    "finalizing",
    "queued",
    "stale",
  ];
  return priorities
    .map((phase) =>
      items.find(
        (item) => item.camera_id === cameraId && item.phase === phase,
      ),
    )
    .find((item) => item !== undefined);
}

export function recordingBlocksStart(
  activity: RecordingActivity | undefined,
): boolean {
  return activity !== undefined && activity.phase !== "stale";
}

export function recordingButtonLabel(
  activity: RecordingActivity | undefined,
  starting: boolean,
): string {
  if (starting) {
    return "Запускаем…";
  }
  if (activity?.phase === "recording") {
    return "Идёт запись";
  }
  if (activity?.phase === "finalizing") {
    return "Сохраняем…";
  }
  if (activity?.phase === "queued") {
    return "В очереди";
  }
  return "Начать запись";
}

export function recordingDescription(
  activity: RecordingActivity | undefined,
  now = Date.now(),
): string | undefined {
  if (!activity) {
    return undefined;
  }
  if (activity.phase === "queued") {
    return "Запись на SSD ожидает запуска";
  }
  if (activity.phase === "finalizing") {
    return "Завершаем и сохраняем файл";
  }
  if (activity.phase === "stale") {
    return "Предыдущее задание не обновляется — можно запустить новое";
  }
  if (!activity.expected_finish_at) {
    return "Запись на SSD выполняется";
  }
  const finishAt = new Date(activity.expected_finish_at).getTime();
  if (!Number.isFinite(finishAt)) {
    return "Запись на SSD выполняется";
  }
  const remainingSec = Math.ceil((finishAt - now) / 1000);
  if (remainingSec <= 0) {
    return "Запись завершается";
  }
  return `Запись на SSD: осталось примерно ${formatRecordingDuration(remainingSec)}`;
}

export function formatRecordingDuration(seconds: number): string {
  if (seconds < 60) {
    return `${seconds} сек`;
  }
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) {
    return `${minutes} мин`;
  }
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours} ч ${remainder} мин` : `${hours} ч`;
}
