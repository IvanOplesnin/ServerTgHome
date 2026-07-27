interface TelegramThemeParams {
  bg_color?: string;
  text_color?: string;
  hint_color?: string;
  link_color?: string;
  button_color?: string;
  button_text_color?: string;
  secondary_bg_color?: string;
  header_bg_color?: string;
  accent_text_color?: string;
  section_bg_color?: string;
  section_header_text_color?: string;
  subtitle_text_color?: string;
  destructive_text_color?: string;
}

interface TelegramDownloadFileParams {
  url: string;
  file_name: string;
}

interface TelegramWebApp {
  initData: string;
  colorScheme?: "light" | "dark";
  themeParams?: TelegramThemeParams;
  ready(): void;
  expand(): void;
  disableVerticalSwipes?(): void;
  setHeaderColor?(color: string): void;
  setBackgroundColor?(color: string): void;
  onEvent?(eventType: string, callback: () => void): void;
  offEvent?(eventType: string, callback: () => void): void;
  downloadFile?(
    params: TelegramDownloadFileParams,
    callback?: (accepted: boolean) => void,
  ): void;
  openLink?(url: string, options?: { try_instant_view?: boolean }): void;
  showAlert?(message: string, callback?: () => void): void;
  HapticFeedback?: {
    impactOccurred(style: "light" | "medium" | "heavy" | "rigid" | "soft"): void;
    notificationOccurred(type: "error" | "success" | "warning"): void;
    selectionChanged(): void;
  };
}

declare global {
  interface Window {
    Telegram?: {
      WebApp?: TelegramWebApp;
    };
  }
}

export function telegramWebApp(): TelegramWebApp | undefined {
  return window.Telegram?.WebApp;
}

export function initializeTelegram(): void {
  const webApp = telegramWebApp();
  if (!webApp) {
    document.documentElement.dataset.telegram = "false";
    return;
  }

  document.documentElement.dataset.telegram = "true";
  applyColorScheme(webApp);
  webApp.ready();
  webApp.expand();
  webApp.disableVerticalSwipes?.();
  webApp.setHeaderColor?.("secondary_bg_color");
  webApp.setBackgroundColor?.("bg_color");
}

export function subscribeToTelegramTheme(): () => void {
  const webApp = telegramWebApp();
  if (!webApp?.onEvent) {
    return () => undefined;
  }
  const update = () => applyColorScheme(webApp);
  webApp.onEvent("themeChanged", update);
  return () => webApp.offEvent?.("themeChanged", update);
}

function applyColorScheme(webApp: TelegramWebApp): void {
  document.documentElement.dataset.theme = webApp.colorScheme ?? "dark";
}

export function getTelegramInitData(): string {
  return telegramWebApp()?.initData ?? "";
}

export function requestTelegramDownload(
  url: string,
  filename: string,
  onRejected: () => void,
): boolean {
  const webApp = telegramWebApp();
  if (!webApp?.downloadFile) {
    return false;
  }

  webApp.downloadFile({ url, file_name: filename }, (accepted) => {
    if (!accepted) {
      onRejected();
    }
  });
  return true;
}

export function notify(type: "error" | "success" | "warning"): void {
  telegramWebApp()?.HapticFeedback?.notificationOccurred(type);
}
