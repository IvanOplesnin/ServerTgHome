export function formatDateTime(value?: string | null): string {
  if (!value) {
    return "Нет данных";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Нет данных";
  }
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatRelativeTime(value?: string | null, now = Date.now()): string {
  if (!value) {
    return "время неизвестно";
  }
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return "время неизвестно";
  }
  const differenceMinutes = Math.round((timestamp - now) / 60_000);
  const formatter = new Intl.RelativeTimeFormat("ru-RU", { numeric: "auto" });
  if (Math.abs(differenceMinutes) < 60) {
    return formatter.format(differenceMinutes, "minute");
  }
  const differenceHours = Math.round(differenceMinutes / 60);
  if (Math.abs(differenceHours) < 48) {
    return formatter.format(differenceHours, "hour");
  }
  return formatter.format(Math.round(differenceHours / 24), "day");
}

export function formatDuration(seconds?: number | null): string {
  if (seconds === undefined || seconds === null || seconds < 0) {
    return "—";
  }
  const wholeSeconds = Math.round(seconds);
  const minutes = Math.floor(wholeSeconds / 60);
  const remainder = wholeSeconds % 60;
  return minutes > 0 ? `${minutes}:${String(remainder).padStart(2, "0")}` : `${remainder} сек`;
}

export function formatBytes(bytes?: number | null): string {
  if (bytes === undefined || bytes === null || bytes < 0) {
    return "—";
  }
  const units = ["Б", "КБ", "МБ", "ГБ"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: unit === 0 ? 0 : 1,
  }).format(value)} ${units[unit]}`;
}

export function safeFilename(value: string | undefined, fallback: string): string {
  const cleaned = value
    ?.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_")
    .trim()
    .slice(0, 120);
  return cleaned || fallback;
}
