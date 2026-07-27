# Telegram Mini App frontend

Мобильный интерфейс для камер, архива видео и климатических датчиков. Frontend
собирается отдельно и общается только с защищённым API `/api/webapp/v1`.

## Локальный запуск

```bash
npm install
npm run dev
```

Vite проксирует `/api` и `/media` на `http://127.0.0.1:8080`. Для настоящего
входа приложение нужно открыть внутри Telegram: тестовый обход проверки
`initData` намеренно не предусмотрен.

Сессия хранится в защищённой HttpOnly cookie. Возвращаемый резервный bearer
token остаётся только в памяти вкладки и не записывается в browser storage.

## Проверки

```bash
npm run typecheck
npm test
npm run build
```

## Расширение вкладок

Новая вкладка добавляется вызовом `registerTab()` в `src/tabs/registry.tsx`.
Backend управляет доступностью, названием и порядком через массив `tabs` в
ответе bootstrap. Неизвестные frontend виды вкладок безопасно пропускаются.

## Live video

`/streams/:camera_id/ticket` возвращает короткоживущий WSS URL и, опционально,
`player_script_url`, `hls_url`, `modes`, `media`. По умолчанию frontend
на iOS сначала использует нативный HLS через HTTPS, а затем при необходимости
переключается на same-origin `/media/video-stream.js` из go2rtc. Скрипты с
другого origin блокируются. Соединение закрывается кнопкой остановки, при
смене камеры или при выгрузке страницы; краткое сворачивание Telegram WebView
не уничтожает поток.

Просмотр и скачивание архивного файла используют один короткоживущий media
ticket. Поэтому `<video>` не зависит от передачи session cookie в Telegram
Desktop WebView.
