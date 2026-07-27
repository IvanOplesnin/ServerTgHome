export interface PlaybackFailure {
  message?: string;
  retryable: boolean;
  renewTicket: boolean;
}

export function classifyVideoPlaybackFailure(
  mediaErrorCode: number | undefined,
): PlaybackFailure {
  switch (mediaErrorCode) {
    case 1:
      return { renewTicket: false, retryable: false };
    case 2:
      return { renewTicket: true, retryable: false };
    case 3:
      return {
        renewTicket: false,
        retryable: false,
        message:
          "Telegram не смог декодировать эту запись. Скачивание оригинала остаётся доступно.",
      };
    case 4:
      return {
        renewTicket: false,
        retryable: false,
        message:
          "Формат этой записи не поддерживается встроенным плеером Telegram. Файл можно скачать.",
      };
    default:
      return {
        renewTicket: false,
        retryable: true,
        message:
          "Не удалось воспроизвести запись во встроенном плеере. Попробуйте ещё раз или скачайте файл.",
      };
  }
}
