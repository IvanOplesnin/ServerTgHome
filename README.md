# Server Tg Home

Основная документация на русском языке. Английская версия: [README.en.md](README.en.md).

Локальный сервис для событий Home Assistant, записи RTSP-видео и отправки сообщений, фото и видео в Telegram.

## Архитектура

- `api`: FastAPI HTTP API для webhook-запросов Home Assistant и aiogram long polling для Telegram-бота.
- `worker`: Dramatiq worker; получает `job_id`, читает детали задачи из БД и выполняет работу.
- `buffer`: поддерживает короткий постоянный RTSP-буфер по каждой камере.
- `retention`: APScheduler-процесс, который следит за размером папки с видео, предупреждает через Telegram и удаляет старые ролики при переполнении.
- `redis`: брокер очереди Dramatiq.
- `postgres`: постоянное хранилище задач, статусов и истории видео.
- `alembic`: миграции схемы базы данных.

Очередь хранит только `job_id`. Payload задачи, статус, попытки выполнения и история хранятся в Postgres.

## Структура проекта

```text
server_tg_home/
  api/            FastAPI-приложение, HTTP-модели и маршруты.
  core/           Настройки, логирование и общий текст статуса.
  database/       SQLAlchemy-сессия, ORM-модели и запуск Alembic.
  integrations/  Внешние системы кроме Telegram: Home Assistant и будущие API.
  jobs/           Создание задач, статусы в БД, очередь Dramatiq и акторы.
  media/          ffmpeg-запись, RTSP-буфер и работа с файлами.
  telegram/       aiogram polling и клиент отправки сообщений в Telegram.
  workers/        Долгоживущие процессы: буфер камер и очистка хранилища.
  cli.py          Точка входа для Docker и локальных команд.
```

Правила добавления новой логики:

- HTTP-обработчики добавляются в `api/`.
- Telegram-команды добавляются в `telegram/polling.py`.
- Новые типы задач добавляются в `jobs/factory.py` и `jobs/processor.py`.
- Прямые вызовы внешних сервисов добавляются в `integrations/`.
- Логика видео, буфера и файлов добавляется в `media/`.
- Долгоживущие циклы и планировщики добавляются в `workers/`.

## Первый запуск

```bash
cp config/config.example.yaml config/config.yaml
mkdir -p data
```

Задайте секреты в `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=123456:token
TELEGRAM_PROXY_URL=socks5://user:password@proxy-host:1080
STH_WEBHOOK_TOKEN=change-me
HOME_ASSISTANT_TOKEN=ha-long-lived-access-token
POSTGRES_DB=server_tg_home
POSTGRES_USER=server_tg_home
POSTGRES_PASSWORD=change-this-password
```

Заполните `telegram.allowed_chat_ids` и `telegram.default_chat_ids` в `config/config.yaml`.
Чтобы узнать `chat_id`, отправьте боту команду `/start`.

Запуск:

```bash
docker compose up --build
```

## Пример webhook из Home Assistant

```yaml
automation:
  - alias: Door open to Telegram
    trigger:
      - platform: state
        entity_id: binary_sensor.door
        to: "on"
    action:
      - service: rest_command.server_tg_home_door_open

rest_command:
  server_tg_home_door_open:
    url: "http://server-host:8080/events/door_open"
    method: POST
    headers:
      X-Webhook-Token: "change-me"
      Content-Type: "application/json"
    payload: '{"entity_id":"binary_sensor.door"}'
```

## Команды Telegram

- `/start`: показывает текущий `chat_id`.
- `/clip entrance 20`: записывает и отправляет 20-секундный клип.
- `/snapshot entrance`: делает и отправляет один кадр с камеры.
- `/ac_on climate.bedroom`: вызывает `climate.turn_on` в Home Assistant.
- `/status`: показывает статус Redis, очереди, БД и хранилища.

## Несколько камер

Добавьте камеры в секцию `cameras`, а события и команды Telegram должны ссылаться на их `id`:

```yaml
cameras:
  entrance:
    rtsp_url: "rtsp://user:password@192.168.1.10:554/stream1"
    buffer_enabled: true
    default_duration_sec: 20

  yard:
    rtsp_url: "rtsp://user:password@192.168.1.11:554/stream1"
    buffer_enabled: true
    default_duration_sec: 20

events:
  door_open:
    camera_id: "entrance"
    duration_sec: 20
    pre_event_sec: 4
    message: "Door opened"

  yard_motion:
    camera_id: "yard"
    duration_sec: 20
    pre_event_sec: 4
    message: "Yard motion"
```

Отдельный контейнер `buffer` на каждую камеру не нужен. Один процесс `buffer` запускает по одному `ffmpeg` процессу на каждую камеру с `buffer_enabled: true`.

Временные сегменты буфера хранятся в:

```text
<buffer.path>/<camera_id>/
```

При стандартном конфиге это:

```text
/data/buffer/entrance/
/data/buffer/yard/
```

`ffmpeg_output_args` используется для постоянного буфера. Обычно здесь лучше оставлять `-c:v copy`, чтобы не перекодировать поток камеры постоянно.
`ffmpeg_clip_output_args` используется только для финального клипа перед отправкой в Telegram; по умолчанию он перекодирует видео в H.264/AAC, чтобы Telegram корректно видел длительность и звук.

Масштабировать обычно нужно `worker`, если растет количество тяжелых задач. `buffer` лучше держать в одном экземпляре, если только камеры не разделены по разным физическим серверам или нагрузка на сеть/CPU не стала слишком высокой.

## Локальная разработка

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
cp config/config.example.yaml config/config.yaml
server-tg-home api --host 0.0.0.0 --port 8080
```

Для запуска вне Docker укажите в `app.database_url` внутри `config/config.yaml` доступный Postgres.
