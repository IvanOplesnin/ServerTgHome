# Server Tg Home

Local service for Home Assistant events, RTSP camera clips and Telegram delivery.

## Architecture

- `api`: FastAPI webhooks for Home Assistant plus aiogram Telegram long polling.
- `worker`: Dramatiq worker; receives `job_id`, loads job details from DB and performs work.
- `buffer`: keeps a short rolling RTSP segment buffer per camera.
- `retention`: APScheduler process that monitors clip folder size, warns via Telegram and deletes old clips when needed.
- `redis`: Dramatiq broker.
- `postgres`: persistent job, status and video history database.
- `alembic`: database schema migrations.

The queue stores only `job_id`. Job payload, status and history live in the database.

## Project Structure

```text
server_tg_home/
  api/            FastAPI app, HTTP request models and routes.
  core/           Settings, logging and cross-cutting status rendering.
  database/       SQLAlchemy session, ORM models and Alembic migration runner.
  integrations/  External systems except Telegram: Home Assistant, future APIs.
  jobs/          Job creation, DB status transitions, Dramatiq queue and actors.
  media/         ffmpeg recording, RTSP buffer segment handling and file storage.
  telegram/      aiogram bot polling and Telegram send client.
  workers/       Long-running non-HTTP processes: camera buffer and retention.
  cli.py         Process entrypoint used by Docker and local commands.
```

When adding features, keep the direction simple:

- HTTP endpoints belong in `api/`.
- Telegram commands belong in `telegram/polling.py`.
- New job types belong in `jobs/factory.py` and `jobs/processor.py`.
- Direct calls to external systems belong in `integrations/`.
- Video/file logic belongs in `media/`.
- Long-running loops and schedulers belong in `workers/`.

## First Run

```bash
cp config/config.example.yaml config/config.yaml
mkdir -p data
```

Set secrets in `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=123456:token
TELEGRAM_PROXY_URL=socks5://user:password@proxy-host:1080
STH_WEBHOOK_TOKEN=change-me
HOME_ASSISTANT_TOKEN=ha-long-lived-access-token
POSTGRES_DB=server_tg_home
POSTGRES_USER=server_tg_home
POSTGRES_PASSWORD=change-this-password
```

Fill `telegram.allowed_chat_ids` and `telegram.default_chat_ids` in `config/config.yaml`.
Send `/start` to the bot to see your `chat_id`.

Start:

```bash
docker compose up --build
```

## Home Assistant Webhook Example

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

## Telegram Commands

- `/start` shows the current chat id.
- `/clip entrance 20` records and sends a 20 second clip.
- `/snapshot entrance` captures and sends one frame.
- `/ac_on climate.bedroom` calls `climate.turn_on` in Home Assistant.
- `/status` shows queue, DB and storage status.

## Multiple Cameras

Add cameras under `cameras` and point events or Telegram commands to their ids:

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

You do not need one buffer worker container per camera. One `buffer` process starts one ffmpeg process per camera with `buffer_enabled: true`.
Temporary buffer segments are stored under:

```text
<buffer.path>/<camera_id>/
```

With the default config this means:

```text
/data/buffer/entrance/
/data/buffer/yard/
```

Start more `worker` replicas only for job execution throughput, not for the camera buffer. Keep a single `buffer` service unless you intentionally split cameras across different machines.

## Local Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
cp config/config.example.yaml config/config.yaml
server-tg-home api --host 0.0.0.0 --port 8080
```

For local development outside Docker, point `app.database_url` in `config/config.yaml`
to a reachable Postgres instance.
