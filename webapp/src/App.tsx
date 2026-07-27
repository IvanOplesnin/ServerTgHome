import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "./api/client";
import type { BootstrapResponse } from "./api/types";
import { ShieldIcon } from "./components/Icons";
import { ErrorState } from "./components/States";
import { resolveTabs } from "./tabs/registry";
import {
  getTelegramInitData,
  initializeTelegram,
  subscribeToTelegramTheme,
} from "./telegram";

type AppState =
  | { status: "loading" }
  | { status: "error"; title: string; message: string }
  | { status: "ready"; bootstrap: BootstrapResponse };

function errorMessage(error: unknown): { title: string; message: string } {
  if (error instanceof ApiError && error.status === 403) {
    return {
      title: "Доступ закрыт",
      message: "Ваш аккаунт не входит в список пользователей Mini App.",
    };
  }
  if (error instanceof ApiError && error.status === 401) {
    return {
      title: "Не удалось подтвердить вход",
      message: "Закройте приложение и откройте его снова из чата с ботом.",
    };
  }
  return {
    title: "Приложение временно недоступно",
    message: error instanceof Error ? error.message : "Попробуйте открыть его немного позже.",
  };
}

export function App(): React.ReactElement {
  const [state, setState] = useState<AppState>({ status: "loading" });
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    initializeTelegram();
    return subscribeToTelegramTheme();
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    async function start(): Promise<void> {
      setState({ status: "loading" });
      try {
        let bootstrap: BootstrapResponse;
        try {
          bootstrap = await api.getBootstrap(controller.signal);
        } catch (error) {
          if (!(error instanceof ApiError) || error.status !== 401) {
            throw error;
          }
          const initData = getTelegramInitData();
          if (!initData) {
            setState({
              status: "error",
              title: "Откройте приложение в Telegram",
              message:
                "Для безопасного входа запустите Mini App кнопкой в групповом чате с ботом.",
            });
            return;
          }
          await api.createSession(initData, controller.signal);
          bootstrap = await api.getBootstrap(controller.signal);
        }
        if (!controller.signal.aborted) {
          setState({
            status: "ready",
            bootstrap: {
              ...bootstrap,
              cameras: bootstrap.cameras ?? [],
            },
          });
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setState({ status: "error", ...errorMessage(error) });
        }
      }
    }

    void start();
    return () => controller.abort();
  }, [reloadKey]);

  if (state.status === "loading") {
    return <AppLoading />;
  }

  if (state.status === "error") {
    return (
      <div className="app-gate">
        <div className="app-gate__brand">
          <span>
            <ShieldIcon />
          </span>
          <strong>Умный дом</strong>
        </div>
        <ErrorState
          message={state.message}
          onRetry={() => setReloadKey((value) => value + 1)}
          title={state.title}
        />
        <p>Доступ предоставляется только администраторам и выбранным участникам группы.</p>
      </div>
    );
  }

  return <AuthenticatedApp bootstrap={state.bootstrap} />;
}

function AuthenticatedApp({
  bootstrap,
}: {
  bootstrap: BootstrapResponse;
}): React.ReactElement {
  const tabs = useMemo(() => resolveTabs(bootstrap.tabs), [bootstrap.tabs]);
  const [activeId, setActiveId] = useState(tabs[0]?.id ?? "");

  useEffect(() => {
    if (!tabs.some((tab) => tab.id === activeId)) {
      setActiveId(tabs[0]?.id ?? "");
    }
  }, [activeId, tabs]);

  const active = tabs.find((tab) => tab.id === activeId) ?? tabs[0];
  const userName =
    bootstrap.user.display_name ??
    [bootstrap.user.first_name, bootstrap.user.last_name].filter(Boolean).join(" ") ??
    "Пользователь";

  if (!active) {
    return (
      <div className="app-gate">
        <ErrorState
          message="Администратор пока не включил ни одной вкладки."
          title="Нет доступных разделов"
        />
      </div>
    );
  }

  const ActiveComponent = active.component;

  return (
    <div className="app-shell">
      <header className="app-topbar">
        <div className="app-brand">
          <span className="app-brand__icon">
            <ShieldIcon />
          </span>
          <span>
            <strong>Home</strong>
            <small>secure access</small>
          </span>
        </div>
        <div className="user-chip" title={userName || "Пользователь"}>
          <span>{(userName || "U").slice(0, 1).toUpperCase()}</span>
          <div>
            <strong>{userName || "Пользователь"}</strong>
            <small>{bootstrap.user.role === "admin" ? "Администратор" : "Наблюдатель"}</small>
          </div>
        </div>
      </header>

      <div className="app-content" id={`tab-panel-${active.id}`} role="tabpanel">
        <ActiveComponent bootstrap={bootstrap} key={active.id} />
      </div>

      <nav aria-label="Разделы приложения" className="bottom-nav" role="tablist">
        <div className="bottom-nav__inner">
          {tabs.map((tab) => {
            const selected = tab.id === active.id;
            return (
              <button
                aria-controls={`tab-panel-${tab.id}`}
                aria-selected={selected}
                className={selected ? "is-active" : ""}
                key={tab.id}
                onClick={() => setActiveId(tab.id)}
                role="tab"
                type="button"
              >
                <span>{tab.icon(selected)}</span>
                {tab.title}
              </button>
            );
          })}
        </div>
      </nav>
    </div>
  );
}

function AppLoading(): React.ReactElement {
  return (
    <div className="app-loading" role="status">
      <div className="app-loading__mark">
        <ShieldIcon />
      </div>
      <strong>Умный дом</strong>
      <span className="spinner" />
      <p>Проверяем доступ…</p>
    </div>
  );
}
