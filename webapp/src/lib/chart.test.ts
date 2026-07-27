import { describe, expect, it } from "vitest";

import { buildLinePath, paddedDomain } from "./chart";

describe("buildLinePath", () => {
  it("creates separate segments around missing points", () => {
    expect(
      buildLinePath([
        { x: 0, y: 10 },
        { x: 5, y: 8 },
        null,
        { x: 9, y: 4 },
      ]),
    ).toBe("M0.00,10.00 L5.00,8.00 M9.00,4.00");
  });
});

describe("paddedDomain", () => {
  it("keeps a useful range for one repeated value", () => {
    const [minimum, maximum] = paddedDomain([22, 22], 2);
    expect(maximum - minimum).toBeGreaterThan(2);
    expect(minimum).toBeLessThan(22);
    expect(maximum).toBeGreaterThan(22);
  });
});
