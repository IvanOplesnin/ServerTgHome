import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { BootstrapResponse } from "../api/types";
import { CamerasTab } from "./CamerasTab";

function bootstrap(role: "admin" | "viewer"): BootstrapResponse {
  return {
    user: {
      id: 42,
      first_name: "Ivan",
      role,
      is_admin: role === "admin",
    },
    cameras: [
      {
        id: "entrance",
        title: "Вход",
        live_available: true,
        health: {
          state: "online",
          available: true,
        },
      },
    ],
    tabs: [],
    climate_rooms: [],
  };
}

describe("CamerasTab recording controls", () => {
  it("renders the recording button for administrators", () => {
    const html = renderToStaticMarkup(
      <CamerasTab bootstrap={bootstrap("admin")} />,
    );

    expect(html).toContain("Начать запись");
    expect(html).toContain("Начать запись: Вход");
  });

  it("does not render recording controls for viewers", () => {
    const html = renderToStaticMarkup(
      <CamerasTab bootstrap={bootstrap("viewer")} />,
    );

    expect(html).not.toContain("Начать запись");
    expect(html).toContain("Смотреть");
  });
});
