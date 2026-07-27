# Agent Context

Этот файл нужен для быстрых следующих заходов агента в проект. Он не заменяет `README.md`, а фиксирует практический контекст: где что искать, как устроены основные потоки и какие вещи нельзя случайно сломать.

## Назначение проекта

`ServerTgHome` - локальный сервис умного дома для связки Home Assistant, RTSP/IP камер, Telegram и go2rtc.

Основные задачи:

- принимать события из Home Assistant по HTTP webhook;
- записывать клипы с камер с pre-event буфером;
- отправлять видео, фото, графики и статусы в Telegram;
- принимать Telegram-команды и inline-кнопки в топиках группы;
- проигрывать голосовые сообщения из Telegram на камерах с двусторонней связью;
- хранить записи на SSD и отдавать выбранные записи обратно в Telegram;
- следить за состоянием камер, диска и сервисов.

## Важные правила

- Не печатать в ответах и логах реальные секреты из `.env`, `config/config.yaml`, `config/go2rtc.yaml`.
- Не коммитить реальные runtime-конфиги. В репозиторий должны попадать только example-файлы и код.
- Перед изменениями читать текущие файлы: в проекте могли быть ручные правки пользователя.
- Для поиска использовать `rg`.
- Для ручных правок использовать `apply_patch`.
- Коммит и push делать только когда пользователь явно попросил.
- При логах с mini PC маскировать RTSP/Tapo URL, Telegram tokens, webhook tokens, Home Assistant tokens.

## Архитектура

Базовый поток задач:

1. `api` или `telegram bot` создает запись job в Postgres.
2. В Redis/Dramatiq кладется `job_id`.
3. Worker забирает `job_id`.
4. Worker читает детали job из Postgres.
5. Worker выполняет работу и обновляет статус.
6. Telegram получает результат или сообщение об ошибке.

Основные контейнеры:

- `api` - FastAPI, HTTP endpoints, healthcheck.
- `worker` - Dramatiq worker для видео, snapshot, статусов, датчиков.
- `graph-worker` - генерация графиков Plotly.
- `audio-worker` - очередь голосовых сообщений и talkback на камеры.
- `buffer` - постоянный pre-event буфер камер.
- `retention` - очистка старых видео/файлов по лимитам.
- `postgres` - основная БД.
- `redis` - брокер Dramatiq.
- `go2rtc` - RTSP/Tapo bridge и playback audio на камеры.
- `miniapp-web` - static-only Nginx с React Telegram Mini App; запускается
  профилем `miniapp`, не терминирует TLS и не проксирует API/media.

Публичный HTTPS для Mini App обслуживает существующий Caddy на Raspberry Pi
(`ssh raps`). В проекте не должно быть второго Caddy. Внешний Caddy проксирует
static frontend на `18082`, FastAPI на trusted port `28080` и разрешенные
go2rtc media маршруты на `21984` с `forward_auth` в FastAPI. Эти три порта
должны быть доступны только с Raspberry Pi; локальные `18080/1984` остаются
loopback, а WebRTC `8555/tcp+udp` идет напрямую на mini PC.

## Где искать код

- `server_tg_home/api/` - FastAPI приложение, webhook endpoints, startup.
- `server_tg_home/telegram/` - aiogram polling, команды, callbacks, панели в топиках.
- `server_tg_home/jobs/` - создание job, Dramatiq tasks, обработка payload.
- `server_tg_home/media/` - ffmpeg запись видео, snapshot, storage, работа с буфером.
- `server_tg_home/audio/` - подготовка голосовых сообщений и playback через go2rtc.
- `server_tg_home/graphs/` - Plotly renderer и генерация графиков.
- `server_tg_home/workers/` - buffer worker и retention worker.
- `server_tg_home/core/` - Pydantic Settings, runtime state, sensors, status.
- `server_tg_home/database/` - SQLAlchemy models, session, migrations helper.
- `alembic/versions/` - миграции БД.
- `scripts/deploy.sh` - установка/обновление на mini PC.
- `config/config.example.yaml` - пример основного конфига.
- `config/go2rtc.example.yaml` - пример go2rtc конфига.

## Основные сценарии

### Клип с камеры

Команда `/clip` или inline-кнопка создает job записи и отправки видео. Для событий двери используется pre-event буфер: несколько секунд до события и основная часть после события.

Ключевые места:

- `server_tg_home/jobs/factory.py`
- `server_tg_home/jobs/processor.py`
- `server_tg_home/media/recorder.py`
- `server_tg_home/workers/buffer.py`

### Snapshot

Команда или кнопка snapshot берет кадр с камеры. Если приходит серая картинка, сначала проверять источник потока, go2rtc stream, ffmpeg stderr и актуальность буфера.

### Запись на SSD

Команда `/record <camera> [seconds]` запускает запись файла без обязательной отправки в Telegram.

Команда `/videos [camera]` показывает последние записи и inline-кнопки для загрузки выбранного файла в Telegram.

Ключевые места:

- `server_tg_home/telegram/polling.py`
- `server_tg_home/jobs/factory.py`
- `server_tg_home/jobs/processor.py`
- `server_tg_home/database/models.py`

### Голосовое на камеру

Голосовое сообщение в топике камеры принимается только от админов. Сервис скачивает voice, готовит audio файл, ставит job в очередь и проигрывает через go2rtc на нужной камере. После playback может отправляться reaction clip: 4 секунды до воспроизведения и несколько секунд после.

Ключевые места:

- `server_tg_home/telegram/polling.py`
- `server_tg_home/audio/`
- `server_tg_home/jobs/processor.py`
- `config/go2rtc.example.yaml`

Для Tapo talkback важны go2rtc stream с `tapo://...` и включенная совместимость со сторонними продуктами в приложении Tapo.

### Температура, влажность и графики

Home Assistant отправляет значения датчиков webhooks. Сервис хранит актуальные значения и историю, Telegram показывает текущие значения и графики.

Ключевые места:

- `server_tg_home/core/sensors.py`
- `server_tg_home/graphs/renderer.py`
- `server_tg_home/telegram/polling.py`
- `alembic/versions/`

## Runtime-конфиги

Реальные конфиги:

- `.env`
- `config/config.yaml`
- `config/go2rtc.yaml`

Они содержат секреты и локальные URL. Не вставлять их содержимое в ответы.

Что обычно настраивается в `config/config.yaml`:

- Telegram bot token, default chat ids, proxy, admin user ids.
- Webhook token для Home Assistant.
- Postgres/Redis URLs.
- Камеры: `id`, `title`, RTSP/go2rtc source, buffer path, topic mapping.
- Telegram topics/panels: `door`, `living`, `bed`, `climate`.
- Storage paths: clips, buffer, audio, graphs, recordings.
- Retention limits и предупреждения по диску.
- Home Assistant base URL и token.

Что обычно настраивается в `config/go2rtc.yaml`:

- RTSP/Tapo streams.
- Streams для video read.
- Streams для talkback/playback.
- Preload/microphone параметры для камер с двусторонней связью.

Текущие логические камеры в проекте:

- `entrance` - входная дверь/коридор.
- `living` - гостиная.
- `bed` - спальня.

Текущие логические панели:

- `door`
- `living`
- `bed`
- `climate`

## Mini PC

SSH alias:

```bash
ssh minipc
```

Основной путь приложения:

```bash
/opt/smarthome/server-tg-home
```

На mini PC используется runtime `compose.yaml`, который может отличаться от репозиторного `docker-compose.yml`.

Особенности mini PC:

- Home Assistant работает на этом же mini PC.
- Zigbee2MQTT занимает порт `8080`.
- ServerTgHome API опубликован как `18080:8080`.
- Порт `18081` уже занят другим локальным сервисом; Mini App static upstream
  использует `18082`.
- Home Assistant должен обращаться к сервису по `http://127.0.0.1:18080`.
- Контейнеры ServerTgHome ходят в Home Assistant через `http://host.docker.internal:8123`.
- Docker Compose может предупреждать, что есть `compose.yaml` и `docker-compose.yml`; на mini PC это ожидаемо.

Типовая проверка mini PC:

```bash
ssh minipc 'cd /opt/smarthome/server-tg-home && docker compose ps'
ssh minipc 'curl -fsS http://127.0.0.1:18080/health'
```

Типовое обновление кода после push:

```bash
ssh minipc 'cd /opt/smarthome/server-tg-home && git pull --ff-only origin main && docker compose up -d --build --force-recreate'
```

Если менялись только runtime-конфиги, код пушить не нужно. Надо сделать backup конфига на mini PC, заменить файл и пересоздать затронутые контейнеры.

## Git

Основной GitHub remote:

```bash
origin git@github-servertghome:IvanOplesnin/ServerTgHome.git
```

Локальный GitLab remote:

```bash
gitlab ssh://git@192.168.1.28:2222/home/ServerTgHome.git
```

Для локального GitLab HTTPS могут мешать proxy/cert настройки. SSH push уже использовался как рабочий вариант.

## Проверки перед завершением

Минимальные локальные проверки:

```bash
.venv/bin/python -m compileall server_tg_home alembic
docker compose config --quiet
git diff --check
```

Проверка загрузки реального конфига допустима, но не печатать значения:

```bash
.venv/bin/python - <<'PY'
from server_tg_home.core.config import load_settings
s = load_settings("config/config.yaml")
print("ok", len(s.cameras), len(s.telegram.panels))
PY
```

При проверках jobs помнить: некоторые factory helpers могут преобразовать пустой список `chat_ids` в дефолтные чаты. Если нужна silent-проверка без Telegram, создавать job напрямую с payload `chat_ids: []` и не использовать helper, который вызывает fallback.

## Известные особенности

- Tapo RTSP может давать нестабильный FPS или длинный GOP; это влияет на длину сегментов буфера.
- После отключения питания камер buffer worker должен восстановить stale ffmpeg process и не использовать старые сегменты как свежие.
- `record_and_send_video` может использовать pre-event buffer.
- `record_video_file` для записи на SSD должен стартовать "сейчас" и не обязан использовать pre-event.
- `/last` отправляет последнюю существующую запись.
- `/videos` должен фильтровать записи, у которых файл уже удален retention.
- Для опасных команд и voice playback проверять `admin_user_ids`.
- Для новых камер нужно синхронно обновлять основной config, go2rtc config, topic mapping, panels и mini PC runtime config.
