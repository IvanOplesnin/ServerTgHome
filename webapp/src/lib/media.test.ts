import { describe, expect, it } from "vitest";

import { classifyVideoPlaybackFailure } from "./media";

describe("media playback failures", () => {
  it("renews a capability ticket only for a network failure", () => {
    expect(classifyVideoPlaybackFailure(2)).toEqual({
      renewTicket: true,
      retryable: false,
    });
    expect(classifyVideoPlaybackFailure(3).renewTicket).toBe(false);
    expect(classifyVideoPlaybackFailure(4).renewTicket).toBe(false);
  });

  it("keeps an aborted playback quiet and explains decode failures", () => {
    expect(classifyVideoPlaybackFailure(1)).toEqual({
      renewTicket: false,
      retryable: false,
    });
    expect(classifyVideoPlaybackFailure(3).message).toContain("декодировать");
    expect(classifyVideoPlaybackFailure(4).message).toContain("не поддерживается");
  });

  it("allows a manual retry only for an unknown player failure", () => {
    expect(classifyVideoPlaybackFailure(undefined).retryable).toBe(true);
    expect(classifyVideoPlaybackFailure(3).retryable).toBe(false);
    expect(classifyVideoPlaybackFailure(4).retryable).toBe(false);
  });
});
