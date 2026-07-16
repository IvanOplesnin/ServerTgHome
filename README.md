# Server Tg Home

Основная документация на русском языке. Английская версия: [README.en.md](README.en.md).

Server Tg Home - локальный сервис для связки Home Assistant, RTSP-камер и Telegram.
Он работает в локальной сети, принимает webhook-события, пишет видео с камер, отправляет сообщения/фото/видео в Telegram, строит графики датчиков и умеет воспроизводить голосовые сообщения Telegram на динамиках поддерживаемых камер.

## Что Умеет Сервис

- Принимать HTTP webhook из Home Assistant.
- По событию, например открытию двери, записывать клип с камеры и отправлять его в Telegram.
- Держать постоянный короткий буфер камеры, чтобы в клипе были секунды до события.
- Записывать видео по команде Telegram `/clip`.
- Делать snapshot по команде `/snapshot`.
- Отправлять последний сохраненный ролик по `/last`.
- Работать с групповыми чатами Telegram и конкретными темами.
- Создавать сообщения-панели с inline-кнопками для быстрых действий в темах.
- Получать температуру и влажность из Home Assistant webhook-запросами.
- Показывать текущие значения датчиков командами `/temp` и `/humidity`.
- Хранить историю датчиков в Postgres.
- Строить Plotly-графики по температуре и влажности, отправлять PNG и HTML.
- Следить за размером хранилища видео, предупреждать о заполнении и удалять старые видео.
- Проверять здоровье камер по свежести RTSP-буфера и отправлять уведомления.
- Показывать состояние диска, очередей, камер и БД.
- Воспроизводить Telegram voice message на динамике камеры через go2rtc.
- После голосового отправлять видео реакции камеры: 4 секунды до воспроизведения, само голосовое и 5 секунд после.
- Работать с Telegram через proxy, заданный в конфиге.

## Архитектура

Сервис разбит на несколько процессов Docker Compose:

- `api`: FastAPI HTTP API и aiogram long polling для Telegram-бота.
- `worker`: Dramatiq worker для обычных задач: видео, snapshot, Home Assistant actions.
- `graph-worker`: Dramatiq worker для Plotly-графиков.
- `audio-worker`: Dramatiq worker для последовательного воспроизведения голосовых на камерах.
- `buffer`: долгоживущий процесс, который запускает по одному `ffmpeg` на каждую камеру с `buffer_enabled: true`.
- `retention`: APScheduler-процесс для очистки видео, аудио, графиков, истории датчиков и healthcheck камер.
- `go2rtc`: media gateway для Tapo two-way audio и restream RTSP.
- `postgres`: БД задач, статусов, видео, аудио и истории датчиков.
- `redis`: брокер очередей Dramatiq.

Основной поток задач:

```text
Home Assistant / Telegram / HTTP API
  -> api создает запись job в Postgres
  -> api кладет job_id в Redis
  -> worker берет job_id
  -> worker читает payload из Postgres
  -> worker пишет файлы, вызывает ffmpeg/go2rtc/Home Assistant/Telegram
  -> worker обновляет статус job в Postgres
```

Очередь хранит только `job_id`. Payload, статус, попытки выполнения и ошибки хранятся в Postgres.

## Структура Проекта

```text
server_tg_home/
  api/            FastAPI-приложение и HTTP endpoints.
  audio/          Подготовка OGG/Opus, конвертация и отправка audio в go2rtc.
  core/           Pydantic Settings, логирование, статус, датчики.
  database/       SQLAlchemy модели, сессии, Alembic migrations.
  graphs/         Plotly-графики и экспорт PNG/HTML.
  integrations/  Home Assistant и будущие внешние интеграции.
  jobs/           Создание job, Dramatiq actors, обработчик задач.
  media/          ffmpeg-запись, snapshot, buffer, storage helpers.
  telegram/       aiogram polling, команды, панели, Telegram client.
  workers/        buffer worker и retention worker.
  cli.py          Точка входа для всех контейнеров.
```

Правила добавления логики:

- HTTP endpoints - `api/`.
- Telegram-команды - `telegram/polling.py`.
- Новые job-типы - `jobs/factory.py` и `jobs/processor.py`.
- Работа с ffmpeg/файлами/буфером - `media/`.
- Графики - `graphs/`.
- Аудио и talkback - `audio/`.
- Интеграции вне Telegram - `integrations/`.
- Долгоживущие циклы и планировщики - `workers/`.

## Требования

Минимально для Ubuntu Server:

- Ubuntu Server 22.04 LTS или 24.04 LTS.
- `git`, `curl`, `ca-certificates`.
- Docker Engine и Docker Compose plugin.
- Пользователь с `sudo` для первичной установки.
- Доступ сервера к GitHub и Docker Hub для установки и обновлений.
- Доступ сервера к Telegram API или к Telegram proxy.
- Доступ сервера к RTSP/go2rtc/камерам в локальной сети.
- Доступ Home Assistant к `http://server-ip:8080`.

Ресурсы:

- Минимум: 2 CPU, 2 GB RAM, 20 GB disk.
- Рекомендуемо для нескольких камер, графиков и H.264 transcode: 4+ CPU, 4+ GB RAM, SSD.
- Для вашего мини-ПК с 8 ядрами / 16 потоками и 32 GB RAM текущая архитектура подходит с запасом.

Открытые порты:

- `8080/tcp`: HTTP API Server Tg Home, доступен в локальной сети.
- `1984/tcp`: go2rtc web/API, в compose опубликован только на `127.0.0.1`.
- `8554/tcp`: RTSP go2rtc внутри Docker network, наружу не опубликован.

## Быстрый Локальный Запуск

```bash
cp config/config.example.yaml config/config.yaml
cp config/go2rtc.example.yaml config/go2rtc.yaml
mkdir -p data
```

Создайте `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=123456:telegram-bot-token
TELEGRAM_PROXY_URL=socks5://user:password@proxy-host:1080
STH_WEBHOOK_TOKEN=change-me
HOME_ASSISTANT_TOKEN=ha-long-lived-access-token
POSTGRES_DB=server_tg_home
POSTGRES_USER=server_tg_home
POSTGRES_PASSWORD=change-this-password
```

Заполните `config/config.yaml`, затем запустите:

```bash
docker compose up --build -d
docker compose logs -f --tail=200
```

Проверка:

```bash
curl http://127.0.0.1:8080/health
```

## HTTP API

Все изменяющие endpoints защищены заголовком:

```http
X-Webhook-Token: <STH_WEBHOOK_TOKEN>
```

Если `app.webhook_token` пустой, проверка токена отключена. Для локальной сети это удобно на этапе настройки, но для постоянной эксплуатации лучше оставить токен включенным.

### `GET /health`

Проверяет, что API поднялся, Redis доступен, и возвращает списки камер/событий/комнат.

```bash
curl http://server-host:8080/health
```

Пример ответа:

```json
{
  "status": "ok",
  "redis": true,
  "queue_length": 0,
  "graph_queue_length": 0,
  "audio_queue_length": 0,
  "cameras": ["entrance", "living"],
  "events": ["door_open"]
}
```

### `GET /status`

Возвращает текстовый статус сервиса, аналогичный Telegram-команде `/status`.

```bash
curl http://server-host:8080/status
```

### `POST /events/{event_id}`

Основной endpoint для событий Home Assistant. `event_id` должен существовать в `events` в конфиге.

```bash
curl -X POST http://server-host:8080/events/door_open \
  -H "X-Webhook-Token: change-me" \
  -H "Content-Type: application/json" \
  -d '{"entity_id":"binary_sensor.main_door_contact"}'
```

Ответ:

```json
{"job_id":"...","status":"queued"}
```

Если уведомления выключены через `/disarm` или событие подавлено `cooldown_sec`/`dedupe_window_sec`, ответ будет:

```json
{"job_id":"","status":"ignored"}
```

### `POST /jobs/record-video`

Создает задачу записи видео напрямую через HTTP API.

```bash
curl -X POST http://server-host:8080/jobs/record-video \
  -H "X-Webhook-Token: change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "camera_id": "entrance",
    "duration_sec": 20,
    "pre_event_sec": 4,
    "chat_ids": [-1001234567890],
    "message_thread_id": 10,
    "message": "Manual HTTP clip"
  }'
```

Поля:

- `camera_id`: id камеры из `cameras`.
- `duration_sec`: длительность клипа, максимум 300 секунд.
- `pre_event_sec`: сколько секунд взять до `event_time`, если доступен buffer.
- `chat_ids`: куда отправить видео. Если `null`, берется `telegram.default_chat_ids`.
- `message_thread_id`: тема Telegram. Если `null`, берется `telegram.default_message_thread_id`.
- `message`: подпись к видео.

### `POST /webhooks/temperatures`

Сохраняет температуру и, опционально, влажность. Старый контракт только с `temperatures` сохраняется.

```bash
curl -X POST http://server-host:8080/webhooks/temperatures \
  -H "X-Webhook-Token: change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "temperatures": {
      "bedroom": 22.3,
      "living_room": 24.3
    },
    "humidities": {
      "bedroom": 52,
      "living_room": 49
    },
    "temperature_unit": "°C",
    "humidity_unit": "%"
  }'
```

Также доступны aliases:

- `POST /webhooks/temperature`
- `POST /webhooks/humidity`
- `POST /webhooks/humidities`

Для `/webhooks/humidity` и `/webhooks/humidities` payload без `humidities` трактуется как влажность.

## Конфигурация

Основные runtime-файлы:

- `.env`: секреты и переменные окружения.
- `config/config.yaml`: основной конфиг сервиса.
- `config/go2rtc.yaml`: конфиг go2rtc, нужен для talkback/restream.
- `data/`: видео, буфер, аудио, графики.

Реальные `.env`, `config/config.yaml`, `config/go2rtc.yaml` не нужно коммитить.

### `.env`

```dotenv
TELEGRAM_BOT_TOKEN=
TELEGRAM_PROXY_URL=
STH_WEBHOOK_TOKEN=
HOME_ASSISTANT_TOKEN=
POSTGRES_DB=server_tg_home
POSTGRES_USER=server_tg_home
POSTGRES_PASSWORD=
```

- `TELEGRAM_BOT_TOKEN`: токен от BotFather.
- `TELEGRAM_PROXY_URL`: proxy для Telegram, например `socks5://user:password@host:1080`. Можно оставить пустым, если Telegram доступен напрямую.
- `STH_WEBHOOK_TOKEN`: токен для HTTP webhook/API. В Home Assistant передается в `X-Webhook-Token`.
- `HOME_ASSISTANT_TOKEN`: long-lived access token Home Assistant для команд вроде `/ac_on`.
- `POSTGRES_*`: имя БД, пользователь и пароль Postgres.

### `app`

```yaml
app:
  database_url: "postgresql+psycopg://${POSTGRES_USER:-server_tg_home}:${POSTGRES_PASSWORD:-server_tg_home_password}@postgres:5432/${POSTGRES_DB:-server_tg_home}"
  redis_url: "redis://redis:6379/0"
  queue_name: "server_tg_home_jobs"
  log_level: "INFO"
  webhook_token: "${STH_WEBHOOK_TOKEN:-}"
  max_job_attempts: 2
```

- `database_url`: Postgres DSN.
- `redis_url`: Redis DSN.
- `queue_name`: очередь обычных задач.
- `log_level`: `DEBUG`, `INFO`, `WARNING`, `ERROR`.
- `webhook_token`: токен проверки HTTP-запросов.
- `max_job_attempts`: число попыток job. Для voice playback учитывайте, что повтор job повторно проиграет голосовое, поэтому ошибки реакционного видео не делают audio job failed.

### `api`

```yaml
api:
  enable_telegram_polling: true
```

Если `false`, HTTP API работает, но бот не читает команды Telegram.

### `telegram`

```yaml
telegram:
  bot_token: "${TELEGRAM_BOT_TOKEN:-}"
  proxy_url: "${TELEGRAM_PROXY_URL:-}"
  allowed_chat_ids: []
  default_chat_ids: []
  default_message_thread_id: null
  admin_user_ids: []
  request_timeout_sec: 180
  polling_timeout_sec: 30
```

- `allowed_chat_ids`: чаты, где бот принимает команды. Если список пустой, команды не разрешены.
- `default_chat_ids`: чаты для автоматических уведомлений, если событие не переопределяет `chat_ids`.
- `default_message_thread_id`: тема Telegram по умолчанию.
- `admin_user_ids`: пользователи, которым доступны опасные команды и voice playback.
- `request_timeout_sec`: timeout отправки больших файлов в Telegram.
- `polling_timeout_sec`: timeout long polling.

Узнать ids:

1. Добавьте бота в чат или тему.
2. Отправьте `/start`.
3. Бот покажет `Chat id`, `User id` и `Topic message_thread_id`.

Если `admin_user_ids` пустой, обычные admin-only команды доступны всем участникам разрешенного чата. Voice playback остается запрещенным, пока список админов явно не задан.

### Telegram Panels

Панель - это сообщение с inline-кнопками, которое можно закрепить в теме.

```yaml
telegram:
  panels:
    door:
      title: "Входная дверь"
      kind: "door"
      chat_id: -1001234567890
      message_thread_id: 10
      camera_id: "entrance"
      video_duration_sec: 20

    climate:
      title: "Температура и влажность"
      kind: "climate"
      chat_id: -1001234567890
      message_thread_id: 20
      room_id: "all"
```

- `kind: door`: кнопки видео и snapshot.
- `kind: climate`: кнопки текущих датчиков и графиков 6ч/12ч/24ч/7д/30д.
- Команды: `/panel door`, `/panel climate`, `/panel all`.

### Telegram Camera Topics

Связь темы Telegram с камерой для voice playback:

```yaml
telegram:
  camera_topics:
    living:
      chat_id: -1001234567890
      message_thread_id: 30
      camera_id: "living"
```

Если админ отправит voice message в эту тему, `audio-worker` воспроизведет его на камере `living`.

### `home_assistant`

```yaml
home_assistant:
  base_url: "http://homeassistant.local:8123"
  token: "${HOME_ASSISTANT_TOKEN:-}"
  request_timeout_sec: 20
```

Используется для команд, которые вызывают Home Assistant API, например `/ac_on climate.bedroom`.

### `temperatures`

```yaml
temperatures:
  default_unit: "°C"
  default_humidity_unit: "%"
  stale_after_sec: 7200
  rooms:
    bedroom:
      title: "Спальня"
    living_room:
      title: "Гостиная"
```

- Ключи `rooms` - стабильные ids, которые приходят в webhook.
- `title` - отображаемое имя.
- `stale_after_sec` - через сколько данные считаются устаревшими.

### `graphs`

```yaml
graphs:
  queue_name: "server_tg_home_graph_jobs"
  path: "/data/graphs"
  default_window: "24h"
  max_window: "30d"
  width: 1200
  height_per_panel: 360
  scale: 2
  history_retention_days: 180
  artifact_retention_days: 14
```

- `history_retention_days`: сколько хранить историю датчиков в Postgres.
- `artifact_retention_days`: сколько хранить PNG/HTML графиков.
- `width`, `height_per_panel`, `scale`: качество экспортируемого PNG.

### `audio`

```yaml
audio:
  enabled: true
  queue_name: "server_tg_home_audio_jobs"
  path: "/data/audio"
  max_duration_sec: 15
  retention_days: 14
  reaction_clip_enabled: true
  reaction_pre_event_sec: 4
  reaction_post_event_sec: 5
  go2rtc_base_url: "http://go2rtc:1984"
  go2rtc_restart_before_playback: true
  go2rtc_restart_wait_sec: 12
  go2rtc_restart_poll_sec: 0.5
  playback_grace_sec: 2
  playback_timeout_sec: 60
  default_codec: "pcma"
```

- `enabled`: включает voice playback.
- `max_duration_sec`: максимальная длительность voice message.
- `retention_days`: сколько хранить исходные и подготовленные аудиофайлы.
- `reaction_clip_enabled`: отправлять видео реакции после voice message.
- `reaction_pre_event_sec`: сколько секунд видео записать до фактического playback.
- `reaction_post_event_sec`: сколько секунд видео записать после playback.
- `go2rtc_restart_before_playback`: перезапускать go2rtc перед playback, чтобы убрать stale talkback-сессии после перезапуска камеры.
- `default_codec`: обычно `pcma` для Tapo.

### `storage`

```yaml
storage:
  path: "/data/clips"
  max_size_mb: 10240
  warning_threshold_percent: 85
  cleanup_target_percent: 75
  delete_batch_size: 10
  warning_cooldown_sec: 3600
  retention_poll_sec: 300
  notify_chat_ids: []
  notify_message_thread_id: null
```

- `max_size_mb`: лимит папки с видео.
- `warning_threshold_percent`: когда отправлять предупреждение.
- `cleanup_target_percent`: до какого процента очищать при переполнении.
- `delete_batch_size`: сколько старых видео удалить за один проход.
- `notify_chat_ids`: куда отправлять предупреждения. Если пусто, используются дефолтные Telegram-настройки в логике уведомлений.

### `buffer`

```yaml
buffer:
  enabled: true
  path: "/data/buffer"
  pre_event_seconds: 4
  segment_seconds: 1
  keep_seconds: 60
  restart_delay_sec: 5
```

`buffer` пишет короткие сегменты по каждой камере. Для события с `pre_event_sec: 4` worker берет нужные сегменты и собирает клип, где есть 4 секунды до события.

Один контейнер `buffer` обслуживает все камеры. Отдельный buffer worker на каждую камеру не нужен.

### `camera_health`

```yaml
camera_health:
  enabled: true
  poll_sec: 60
  stale_after_sec: null
  startup_grace_sec: 120
  notify_recovery: true
  notify_chat_ids: []
  notify_message_thread_id: null
```

Healthcheck смотрит, появляются ли свежие buffer-сегменты. Если камера перестала писать сегменты, сервис отправляет уведомление.

Если `stale_after_sec: null`, порог считается автоматически из настроек буфера.

### `cameras`

```yaml
cameras:
  entrance:
    rtsp_url: "rtsp://user:password@192.168.1.10:554/stream1"
    ffmpeg_url: null
    buffer_enabled: true
    speaker_enabled: false
    go2rtc_stream: null
    speaker_audio_codec: "pcma"
    default_duration_sec: 20
    ffmpeg_input_args:
      - "-rtsp_transport"
      - "tcp"
    ffmpeg_output_args:
      - "-map"
      - "0:v:0"
      - "-map"
      - "0:a?"
      - "-c:v"
      - "copy"
      - "-c:a"
      - "aac"
      - "-b:a"
      - "128k"
    ffmpeg_clip_output_args:
      - "-map"
      - "0:v:0"
      - "-map"
      - "0:a?"
      - "-c:v"
      - "libx264"
      - "-preset"
      - "veryfast"
      - "-crf"
      - "23"
      - "-pix_fmt"
      - "yuv420p"
      - "-c:a"
      - "aac"
      - "-b:a"
      - "128k"
      - "-movflags"
      - "+faststart"
```

- `rtsp_url`: прямой RTSP камеры.
- `ffmpeg_url`: источник для ffmpeg. Если задан, clips/snapshot/buffer читают его вместо `rtsp_url`.
- `buffer_enabled`: писать rolling buffer для камеры.
- `speaker_enabled`: разрешить voice playback на камере.
- `go2rtc_stream`: имя stream в go2rtc.
- `speaker_audio_codec`: codec для talkback, обычно `pcma`.
- `default_duration_sec`: длительность `/clip <camera>` без указания секунд.
- `ffmpeg_input_args`: параметры входа.
- `ffmpeg_output_args`: параметры постоянного буфера. Обычно лучше `-c:v copy`, чтобы не грузить CPU постоянно.
- `ffmpeg_clip_output_args`: параметры финального клипа для Telegram. H.264/AAC надежнее распознается клиентами Telegram.

Для Tapo с talkback лучше использовать:

```yaml
cameras:
  living:
    rtsp_url: "rtsp://camera-user:camera-password@192.168.1.26:554/stream1"
    ffmpeg_url: "rtsp://go2rtc:8554/living"
    buffer_enabled: true
    speaker_enabled: true
    go2rtc_stream: "living"
    speaker_audio_codec: "pcma"
    default_duration_sec: 20
```

Так камера получает меньше прямых RTSP-сессий: go2rtc держит соединение с камерой, а сервис читает restream `rtsp://go2rtc:8554/living`.

### `events`

```yaml
events:
  door_open:
    camera_id: "entrance"
    duration_sec: 20
    pre_event_sec: 4
    cooldown_sec: 30
    dedupe_window_sec: 5
    chat_ids: []
    message_thread_id: null
    message: "Door opened"
```

- `camera_id`: камера, с которой писать клип.
- `duration_sec`: общая длительность клипа.
- `pre_event_sec`: сколько секунд взять до события из buffer.
- `cooldown_sec`: минимальный интервал между задачами этого события.
- `dedupe_window_sec`: подавление повторного webhook с тем же payload.
- `chat_ids`: куда отправлять именно это событие. Если пусто, используются `telegram.default_chat_ids`.
- `message_thread_id`: тема именно для этого события.
- `message`: подпись к видео.

## go2rtc

`config/go2rtc.yaml` нужен для камер с talkback и для restream.

Пример Tapo C200/C210:

```yaml
api:
  listen: ":1984"

rtsp:
  listen: ":8554"

streams:
  living:
    - tapo://TAPO_CLOUD_PASSWORD@192.168.1.26?subtype=0
    - rtsp://CAMERA_ACCOUNT:CAMERA_PASSWORD@192.168.1.26:554/stream1

preload:
  living: "video=all&audio=all&microphone=all"
```

Важно:

- `TAPO_CLOUD_PASSWORD` - пароль от учетной записи Tapo, через которую приложение Tapo умеет talkback.
- `CAMERA_ACCOUNT` и `CAMERA_PASSWORD` - учетная запись камеры для RTSP.
- `preload` держит подключение готовым, включая microphone/talkback.
- Реальный `config/go2rtc.yaml` содержит секреты и не должен попадать в git.

## Типовые Конфигурации

### 1. Только входная дверь и Telegram видео

```yaml
telegram:
  allowed_chat_ids:
    - -1001234567890
  default_chat_ids:
    - -1001234567890
  default_message_thread_id: 10
  admin_user_ids:
    - 123456789

cameras:
  entrance:
    rtsp_url: "rtsp://user:password@192.168.1.10:554/stream1"
    buffer_enabled: true
    default_duration_sec: 20

events:
  door_open:
    camera_id: "entrance"
    duration_sec: 20
    pre_event_sec: 4
    cooldown_sec: 30
    dedupe_window_sec: 5
    message: "Открыта входная дверь"
```

### 2. Групповой чат с темами

```yaml
telegram:
  allowed_chat_ids:
    - -1001234567890
  default_chat_ids:
    - -1001234567890
  admin_user_ids:
    - 123456789
  panels:
    door:
      title: "Входная дверь"
      kind: "door"
      chat_id: -1001234567890
      message_thread_id: 10
      camera_id: "entrance"
      video_duration_sec: 20
    climate:
      title: "Температура и влажность"
      kind: "climate"
      chat_id: -1001234567890
      message_thread_id: 20
      room_id: "all"

events:
  door_open:
    camera_id: "entrance"
    chat_ids:
      - -1001234567890
    message_thread_id: 10
```

После запуска отправьте:

```text
/panel door
/panel climate
```

### 3. Tapo камера в гостиной с voice playback и видео реакции

`config/config.yaml`:

```yaml
telegram:
  admin_user_ids:
    - 123456789
  camera_topics:
    living:
      chat_id: -1001234567890
      message_thread_id: 30
      camera_id: "living"

audio:
  enabled: true
  reaction_clip_enabled: true
  reaction_pre_event_sec: 4
  reaction_post_event_sec: 5

cameras:
  living:
    rtsp_url: "rtsp://camera-user:camera-password@192.168.1.26:554/stream1"
    ffmpeg_url: "rtsp://go2rtc:8554/living"
    buffer_enabled: true
    speaker_enabled: true
    go2rtc_stream: "living"
    speaker_audio_codec: "pcma"
```

`config/go2rtc.yaml`:

```yaml
streams:
  living:
    - tapo://TAPO_CLOUD_PASSWORD@192.168.1.26?subtype=0
    - rtsp://camera-user:camera-password@192.168.1.26:554/stream1

preload:
  living: "video=all&audio=all&microphone=all"
```

Когда админ отправляет voice message в тему `message_thread_id: 30`, бот:

1. Сохраняет исходный OGG/Opus.
2. Конвертирует его в `PCMA/8000 mono`.
3. Перед playback запускает видеозапись через `ffmpeg_url`.
4. Ждет 4 секунды.
5. Воспроизводит голосовое через go2rtc.
6. Записывает еще 5 секунд после.
7. Отправляет видео реакции в ту же тему.

### 4. Несколько камер

```yaml
cameras:
  entrance:
    rtsp_url: "rtsp://user:password@192.168.1.10:554/stream1"
    buffer_enabled: true
    default_duration_sec: 20

  living:
    rtsp_url: "rtsp://user:password@192.168.1.26:554/stream1"
    ffmpeg_url: "rtsp://go2rtc:8554/living"
    buffer_enabled: true
    speaker_enabled: true
    go2rtc_stream: "living"

  yard:
    rtsp_url: "rtsp://user:password@192.168.1.11:554/stream1"
    buffer_enabled: true
    default_duration_sec: 20

events:
  door_open:
    camera_id: "entrance"
    duration_sec: 20
    pre_event_sec: 4

  yard_motion:
    camera_id: "yard"
    duration_sec: 20
    pre_event_sec: 4
```

Папки буфера:

```text
/data/buffer/entrance/
/data/buffer/living/
/data/buffer/yard/
```

### 5. Только датчики и графики без камер

```yaml
telegram:
  allowed_chat_ids:
    - -1001234567890
  default_chat_ids:
    - -1001234567890

temperatures:
  rooms:
    bedroom:
      title: "Спальня"
    living_room:
      title: "Гостиная"

graphs:
  history_retention_days: 180
  artifact_retention_days: 14

cameras: {}
events: {}
```

Home Assistant будет слать `/webhooks/temperatures`, а Telegram-команды `/temp`, `/humidity`, `/analytics`, `/graph` будут работать без камер.

## Home Assistant

### Открытие двери

```yaml
automation:
  - alias: Открытие входной двери - отправить в Telegram
    trigger:
      - platform: state
        entity_id: binary_sensor.main_door_contact
        from: "off"
        to: "on"
    action:
      - service: rest_command.server_tg_home_door_open
    mode: single

rest_command:
  server_tg_home_door_open:
    url: "http://192.168.1.17:8080/events/door_open"
    method: POST
    headers:
      X-Webhook-Token: "change-me"
      Content-Type: "application/json"
    payload: '{"entity_id":"binary_sensor.main_door_contact"}'
```

### Температура и влажность

```yaml
automation:
  - alias: Отправить климат комнат в Server Tg Home
    trigger:
      - platform: state
        entity_id:
          - sensor.bedroom_temperature
          - sensor.living_room_temperature
          - sensor.bedroom_humidity
          - sensor.living_room_humidity
    action:
      - service: rest_command.server_tg_home_room_climate

rest_command:
  server_tg_home_room_climate:
    url: "http://192.168.1.17:8080/webhooks/temperatures"
    method: POST
    headers:
      X-Webhook-Token: "change-me"
      Content-Type: "application/json"
    payload: >
      {
        "temperatures": {
          "bedroom": "{{ states('sensor.bedroom_temperature') }}",
          "living_room": "{{ states('sensor.living_room_temperature') }}"
        },
        "humidities": {
          "bedroom": "{{ states('sensor.bedroom_humidity') }}",
          "living_room": "{{ states('sensor.living_room_humidity') }}"
        },
        "temperature_unit": "°C",
        "humidity_unit": "%"
      }
```

## Telegram Команды

Бот регистрирует Telegram command menu, поэтому при вводе `/` клиент показывает команды.

- `/start`: показать `chat_id`, `user_id`, `message_thread_id`.
- `/help`: список команд.
- `/cameras`: состояние камер и буфера.
- `/clip entrance 20`: записать и отправить 20-секундный клип.
- `/last entrance`: отправить последний сохраненный ролик.
- `/snapshot entrance`: сделать и отправить кадр.
- `/arm`: включить автоматические уведомления.
- `/disarm`: выключить автоматические уведомления.
- `/mute 1h`: временно отключить уведомления.
- `/mute off`: снять mute.
- `/temp`: температура и влажность.
- `/humidity`: влажность.
- `/analytics all 24h`: min/avg/max/latest по датчикам.
- `/graph bedroom 24h`: график спальни за 24 часа.
- `/graph all 7d`: общий график за 7 дней.
- `/graph living_room 24h humidity`: только влажность гостиной.
- `/disk`: состояние хранилища.
- `/panel door`: отправить панель с кнопками.
- `/panel all`: отправить все панели.
- `/ac_on climate.bedroom`: вызвать `climate.turn_on` в Home Assistant.
- `/status`: статус Redis, очередей, БД и файлов.

Admin-only команды: `/clip`, `/last`, `/snapshot`, `/arm`, `/disarm`, `/mute`, `/ac_on`, `/panel`, кнопки камеры в `door` панели и voice playback.

## Графики

Команда `/graph` создает job в очереди `graphs.queue_name`.
`graph-worker` читает `sensor_readings`, строит Plotly-график и отправляет:

- PNG для быстрого просмотра.
- HTML-документ с интерактивным Plotly-графиком.

Примеры:

```text
/graph bedroom 24h
/graph all 7d
/graph living_room 24h humidity
/graph all 30d temperature
```

Поддерживаемые окна задаются парсером команд и ограничиваются `graphs.max_window`.

## Деплой На Ubuntu Server

Рекомендуемый способ для мини-ПК: держать репозиторий на сервере и собирать Docker-образы локально через `docker compose build`.
Отдельный Docker registry пока не нужен.

### 1. Подготовить сервер

На чистом Ubuntu Server нужен пользователь с `sudo`.
Скрипт `deploy.sh init` сам установит Docker Engine и Docker Compose plugin, если их нет.

Если хотите установить базовые пакеты вручную:

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates openssh-client
```

### 2. SSH deploy key для GitHub

Если репозиторий приватный:

```bash
ssh-keygen -t ed25519 -C "server-tg-home-deploy-$(hostname)" -f ~/.ssh/server_tg_home_github -N ""

cat >> ~/.ssh/config <<'EOF'
Host github-servertghome
  HostName github.com
  User git
  IdentityFile ~/.ssh/server_tg_home_github
  IdentitiesOnly yes
EOF

cat ~/.ssh/server_tg_home_github.pub
```

Добавьте public key в GitHub repository settings как Deploy key. Для обновлений достаточно read-only.

Если репозиторий уже склонирован и `scripts/deploy.sh` доступен:

```bash
./scripts/deploy.sh ssh-key
```

### 3. Первичная установка

```bash
sudo mkdir -p /opt/server-tg-home
sudo chown "$USER:$USER" /opt/server-tg-home
git clone git@github-servertghome:IvanOplesnin/ServerTgHome.git /opt/server-tg-home
cd /opt/server-tg-home

./scripts/deploy.sh init
```

`init` делает:

- проверяет и устанавливает базовые зависимости;
- устанавливает Docker Engine и Compose plugin, если нужно;
- клонирует/обновляет репозиторий;
- создает `.env`;
- создает `config/config.yaml`;
- создает `config/go2rtc.yaml`;
- создает `data/`.

Если runtime-файлы созданы впервые, стек не стартует автоматически. Заполните:

```bash
nano .env
nano config/config.yaml
nano config/go2rtc.yaml
```

Затем:

```bash
./scripts/deploy.sh doctor
./scripts/deploy.sh deploy
```

### 4. Проверка

```bash
./scripts/deploy.sh status
curl http://127.0.0.1:8080/health
docker compose logs -f --tail=200
```

`doctor` проверяет:

- ОС и архитектуру;
- наличие `git`, `curl`, Docker CLI, Compose plugin;
- доступ текущего пользователя к Docker daemon;
- git branch/revision;
- наличие `.env`, `config/config.yaml`, `config/go2rtc.yaml`;
- валидность `docker compose config`;
- состояние compose services;
- свободное место на диске.

### 5. Обновление

```bash
cd /opt/server-tg-home
./scripts/deploy.sh deploy
```

Скрипт:

- делает `git fetch` и `git pull --ff-only`;
- не продолжает deploy, если есть локальные изменения tracked-файлов;
- пересобирает application images при изменениях;
- запускает `docker compose up -d --remove-orphans`;
- проверяет `/health`.

Чтобы также обновить базовые images `postgres`, `redis`, `go2rtc`:

```bash
STH_PULL_IMAGES=1 ./scripts/deploy.sh deploy
```

Принудительно пересобрать и пересоздать:

```bash
STH_FORCE=1 ./scripts/deploy.sh deploy
```

### 6. Автообновление systemd timer

```bash
cd /opt/server-tg-home
./scripts/deploy.sh install-timer
```

По умолчанию timer запускает deploy каждые 10 минут.
Меняется переменной:

```bash
STH_UPDATE_INTERVAL=30min ./scripts/deploy.sh install-timer
```

Удалить timer:

```bash
./scripts/deploy.sh uninstall-timer
```

### 7. Полезные команды

```bash
./scripts/deploy.sh doctor
./scripts/deploy.sh status
./scripts/deploy.sh logs
./scripts/deploy.sh restart
```

### 8. Backup И Restore

Backup:

```bash
./scripts/backup.sh
```

В backup попадают:

- Postgres dump;
- `.env`;
- `config/config.yaml`;
- `config/go2rtc.yaml`, если есть.

Видео не включаются по умолчанию. Чтобы включить `data/`:

```bash
STH_BACKUP_INCLUDE_DATA=1 ./scripts/backup.sh
```

Restore:

```bash
./scripts/restore.sh backups/server-tg-home-YYYYMMDD-HHMMSS.tar.gz
```

Перед восстановлением текущие `.env`, `config/config.yaml`, `config/go2rtc.yaml` и `data` сохраняются рядом с суффиксом `restore-before-*`.

## Эксплуатация И Диагностика

Проверить контейнеры:

```bash
docker compose ps
```

Логи API и бота:

```bash
docker compose logs -f api
```

Логи записи видео:

```bash
docker compose logs -f worker buffer
```

Логи voice playback:

```bash
docker compose logs -f audio-worker go2rtc
```

Проверить RTSP через go2rtc внутри Docker:

```bash
docker compose exec worker ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1 rtsp://go2rtc:8554/living
```

Частые проблемы:

- `401 Invalid webhook token`: не совпадает `STH_WEBHOOK_TOKEN` и `X-Webhook-Token`.
- Telegram не отвечает: проверьте `TELEGRAM_BOT_TOKEN`, `TELEGRAM_PROXY_URL`, доступ к proxy.
- Нет видео с Tapo при voice playback: используйте `ffmpeg_url: rtsp://go2rtc:8554/<stream>`, чтобы не создавать лишние прямые RTSP-сессии.
- Voice playback пишет успех, но звука нет: проверьте `go2rtc_restart_before_playback`, `preload` с `microphone=all`, совместимость сторонних продуктов в Tapo.
- Snapshot серый: проверьте, что buffer пишет свежие сегменты, или что `ffmpeg_url` доступен.
- Графики пустые: проверьте, что Home Assistant реально отправляет `/webhooks/temperatures` и что `sensor_readings` пополняется.

## Локальная Разработка

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
cp config/config.example.yaml config/config.yaml
cp config/go2rtc.example.yaml config/go2rtc.yaml
server-tg-home api --host 0.0.0.0 --port 8080
```

Для локального запуска вне Docker укажите в `app.database_url` и `app.redis_url` доступные Postgres/Redis.

Проверки перед коммитом:

```bash
python -m compileall server_tg_home alembic
docker compose config --quiet
git diff --check
```
