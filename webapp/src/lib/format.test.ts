import { describe, expect, it } from "vitest";

import { formatBytes, formatDuration, safeFilename } from "./format";

describe("format helpers", () => {
  it("formats durations and file sizes for the archive", () => {
    expect(formatDuration(75)).toBe("1:15");
    expect(formatBytes(1024)).toBe("1 КБ");
  });

  it("removes unsafe filename characters", () => {
    expect(safeFilename('entrance:12/30?.mp4', "video.mp4")).toBe(
      "entrance_12_30_.mp4",
    );
  });
});
