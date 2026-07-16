# Server Tg Home

Primary documentation is in Russian: [README.md](README.md).

Server Tg Home is a local service that connects Home Assistant, RTSP cameras and Telegram.
It runs in a LAN, receives webhooks, records camera clips, sends messages/photos/videos to Telegram, renders sensor graphs and can play Telegram voice messages on supported camera speakers.

## Features

- Receive Home Assistant HTTP webhooks.
- Record and send a camera clip when an event happens, for example when a door opens.
- Keep a short rolling camera buffer so clips can include seconds before the event.
- Record clips from Telegram with `/clip`.
- Capture snapshots with `/snapshot`.
- Send the latest saved video with `/last`.
- Work with Telegram group chats and forum topics.
- Create pinned inline-button panels for frequently used actions.
- Receive temperature and humidity values from Home Assistant webhooks.
- Show current sensor values with `/temp` and `/humidity`.
- Store sensor history in Postgres.
- Render Plotly temperature/humidity graphs and send PNG plus HTML.
- Monitor video storage size, warn before cleanup and delete old clips when needed.
- Monitor camera health by checking fresh RTSP buffer segments.
- Show disk, queue, camera and database status.
- Play Telegram voice messages on camera speakers via go2rtc.
- Send a camera reaction clip after voice playback: 4 seconds before playback, the voice message itself, and 5 seconds after.
- Use a Telegram proxy from configuration.

## Architecture

Docker Compose services:

- `api`: FastAPI HTTP API and aiogram long polling for the Telegram bot.
- `worker`: Dramatiq worker for clips, snapshots and Home Assistant actions.
- `graph-worker`: Dramatiq worker for Plotly graphs.
- `audio-worker`: sequential Dramatiq worker for camera speaker playback.
- `buffer`: long-running process that starts one `ffmpeg` process per buffered camera.
- `retention`: APScheduler process for cleanup and camera health checks.
- `go2rtc`: media gateway for Tapo two-way audio and RTSP restreams.
- `postgres`: persistent database for jobs, video/audio records and sensor history.
- `redis`: Dramatiq broker.

Job flow:

```text
Home Assistant / Telegram / HTTP API
  -> api creates a job row in Postgres
  -> api pushes job_id to Redis
  -> worker consumes job_id
  -> worker reads payload from Postgres
  -> worker writes files and calls ffmpeg/go2rtc/Home Assistant/Telegram
  -> worker updates job status in Postgres
```

Redis stores only `job_id`. Payload, attempts, status and errors live in Postgres.

## Project Layout

```text
server_tg_home/
  api/            FastAPI app and HTTP endpoints.
  audio/          OGG/Opus preparation, conversion and go2rtc playback.
  core/           Pydantic Settings, logging, status and sensors.
  database/       SQLAlchemy models, sessions and Alembic migrations.
  graphs/         Plotly rendering and PNG/HTML export.
  integrations/  Home Assistant and future external integrations.
  jobs/           Job factories, Dramatiq actors and processor.
  media/          ffmpeg recording, snapshots, buffer and storage helpers.
  telegram/       aiogram polling, commands, panels and Telegram client.
  workers/        Buffer and retention workers.
  cli.py          Entry point for every container.
```

## Requirements

Minimum Ubuntu Server environment:

- Ubuntu Server 22.04 LTS or 24.04 LTS.
- `git`, `curl`, `ca-certificates`.
- Docker Engine and Docker Compose plugin.
- A user with `sudo` for initial setup.
- Network access to GitHub and Docker Hub.
- Network access to Telegram API or a Telegram proxy.
- LAN access to RTSP/go2rtc/cameras.
- Home Assistant access to `http://server-ip:8080`.

Resources:

- Minimum: 2 CPU, 2 GB RAM, 20 GB disk.
- Recommended for multiple cameras, graphs and H.264 transcoding: 4+ CPU, 4+ GB RAM, SSD.
- An 8-core / 16-thread mini PC with 32 GB RAM is more than enough for the current architecture.

Ports:

- `8080/tcp`: Server Tg Home HTTP API, exposed to the LAN.
- `1984/tcp`: go2rtc web/API, bound to `127.0.0.1` by compose.
- `8554/tcp`: go2rtc RTSP inside the Docker network, not exposed to the host.

## Quick Start

```bash
cp config/config.example.yaml config/config.yaml
cp config/go2rtc.example.yaml config/go2rtc.yaml
mkdir -p data
```

Create `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=123456:telegram-bot-token
TELEGRAM_PROXY_URL=socks5://user:password@proxy-host:1080
STH_WEBHOOK_TOKEN=change-me
HOME_ASSISTANT_TOKEN=ha-long-lived-access-token
POSTGRES_DB=server_tg_home
POSTGRES_USER=server_tg_home
POSTGRES_PASSWORD=change-this-password
```

Edit `config/config.yaml`, then run:

```bash
docker compose up --build -d
docker compose logs -f --tail=200
curl http://127.0.0.1:8080/health
```

## HTTP API

Mutating endpoints use:

```http
X-Webhook-Token: <STH_WEBHOOK_TOKEN>
```

If `app.webhook_token` is empty, token validation is disabled. That is useful during setup, but a token is recommended for normal operation.

### `GET /health`

Returns API and queue health plus configured cameras/events/rooms.

```bash
curl http://server-host:8080/health
```

### `GET /status`

Returns a text service status similar to Telegram `/status`.

```bash
curl http://server-host:8080/status
```

### `POST /events/{event_id}`

Main Home Assistant event endpoint. `event_id` must exist in `events`.

```bash
curl -X POST http://server-host:8080/events/door_open \
  -H "X-Webhook-Token: change-me" \
  -H "Content-Type: application/json" \
  -d '{"entity_id":"binary_sensor.main_door_contact"}'
```

Queued response:

```json
{"job_id":"...","status":"queued"}
```

Suppressed response:

```json
{"job_id":"","status":"ignored"}
```

### `POST /jobs/record-video`

Creates a video recording job directly from HTTP.

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

### `POST /webhooks/temperatures`

Stores temperature and optional humidity values. The old payload with only `temperatures` remains supported.

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

Aliases:

- `POST /webhooks/temperature`
- `POST /webhooks/humidity`
- `POST /webhooks/humidities`

## Configuration

Runtime files:

- `.env`: secrets and environment variables.
- `config/config.yaml`: main service config.
- `config/go2rtc.yaml`: go2rtc config for talkback/restreams.
- `data/`: clips, buffer segments, audio and graphs.

Do not commit real `.env`, `config/config.yaml` or `config/go2rtc.yaml`.

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

- `TELEGRAM_BOT_TOKEN`: BotFather token.
- `TELEGRAM_PROXY_URL`: optional Telegram proxy, for example `socks5://user:password@host:1080`.
- `STH_WEBHOOK_TOKEN`: token passed as `X-Webhook-Token`.
- `HOME_ASSISTANT_TOKEN`: long-lived access token for commands like `/ac_on`.
- `POSTGRES_*`: Postgres database, user and password.

### Main Sections

`app`:

- `database_url`: Postgres DSN.
- `redis_url`: Redis DSN.
- `queue_name`: main Dramatiq queue.
- `log_level`: `DEBUG`, `INFO`, `WARNING`, `ERROR`.
- `webhook_token`: HTTP token.
- `max_job_attempts`: job retry attempts.

`api`:

- `enable_telegram_polling`: enables Telegram bot polling in the `api` service.

`telegram`:

- `bot_token`, `proxy_url`: Telegram connection settings.
- `allowed_chat_ids`: chats where commands are accepted.
- `default_chat_ids`: default notification targets.
- `default_message_thread_id`: default Telegram topic.
- `admin_user_ids`: admin users for dangerous actions and voice playback.
- `panels`: pinned inline-button panels.
- `camera_topics`: maps Telegram topics to cameras for voice playback.
- `request_timeout_sec`: timeout for large Telegram uploads.

`home_assistant`:

- `base_url`: Home Assistant URL.
- `token`: long-lived access token.
- `request_timeout_sec`: request timeout.

`temperatures`:

- `rooms`: room ids and display titles.
- `default_unit`, `default_humidity_unit`: units.
- `stale_after_sec`: when data is considered stale.

`graphs`:

- `queue_name`: graph queue.
- `path`: graph artifact directory.
- `default_window`, `max_window`: graph windows.
- `width`, `height_per_panel`, `scale`: PNG export quality.
- `history_retention_days`: sensor history retention.
- `artifact_retention_days`: PNG/HTML retention.

`audio`:

- `enabled`: enables voice playback.
- `max_duration_sec`: max accepted voice message duration.
- `retention_days`: audio file retention.
- `reaction_clip_enabled`: send reaction video after voice playback.
- `reaction_pre_event_sec`: video seconds before playback.
- `reaction_post_event_sec`: video seconds after playback.
- `go2rtc_restart_before_playback`: restart go2rtc before playback to avoid stale talkback sessions.
- `default_codec`: usually `pcma` for Tapo.

`storage`:

- `path`: clip directory.
- `max_size_mb`: storage limit.
- `warning_threshold_percent`: warning threshold.
- `cleanup_target_percent`: cleanup target.
- `delete_batch_size`: maximum deleted clips per cleanup pass.
- `notify_chat_ids`, `notify_message_thread_id`: storage notifications.

`buffer`:

- `enabled`: enables rolling buffer.
- `path`: buffer directory.
- `pre_event_seconds`: default pre-event duration.
- `segment_seconds`: segment length.
- `keep_seconds`: how long to keep buffer segments.
- `restart_delay_sec`: ffmpeg restart delay.

`camera_health`:

- checks fresh buffer segments and notifies when cameras stop producing them.

`cameras`:

- `rtsp_url`: direct RTSP URL.
- `ffmpeg_url`: ffmpeg input override. Use `rtsp://go2rtc:8554/<stream>` for Tapo cameras with talkback.
- `buffer_enabled`: enables buffer for this camera.
- `speaker_enabled`: enables voice playback.
- `go2rtc_stream`: go2rtc stream name.
- `speaker_audio_codec`: usually `pcma`.
- `default_duration_sec`: default `/clip` duration.
- `ffmpeg_input_args`: ffmpeg input options.
- `ffmpeg_output_args`: rolling buffer output options.
- `ffmpeg_clip_output_args`: final Telegram clip output options.

`events`:

- `camera_id`: camera to record.
- `duration_sec`: clip duration.
- `pre_event_sec`: seconds before the event from buffer.
- `cooldown_sec`: minimum interval between jobs for the event.
- `dedupe_window_sec`: suppress repeated identical payloads.
- `chat_ids`, `message_thread_id`, `message`: Telegram target and caption.

## go2rtc

Example for Tapo C200/C210:

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

Notes:

- `TAPO_CLOUD_PASSWORD` is the Tapo account password used by the Tapo app for talkback.
- `CAMERA_ACCOUNT` and `CAMERA_PASSWORD` are RTSP camera credentials.
- `preload` keeps the connection ready, including microphone/talkback.
- The real `config/go2rtc.yaml` contains secrets and must not be committed.

## Configuration Examples

### Door Camera Only

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
    message: "Door opened"
```

### Telegram Group Topics

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
      title: "Front door"
      kind: "door"
      chat_id: -1001234567890
      message_thread_id: 10
      camera_id: "entrance"
      video_duration_sec: 20
```

Run `/panel door` and pin the bot message in the topic.

### Tapo Camera With Voice Playback And Reaction Clips

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

When an admin sends a voice message in the mapped topic, the bot stores it, converts it to `PCMA/8000 mono`, plays it through go2rtc and sends a reaction clip back to the same topic.

### Sensors And Graphs Without Cameras

```yaml
telegram:
  allowed_chat_ids:
    - -1001234567890
  default_chat_ids:
    - -1001234567890

temperatures:
  rooms:
    bedroom:
      title: "Bedroom"
    living_room:
      title: "Living room"

cameras: {}
events: {}
```

## Home Assistant

Door event:

```yaml
automation:
  - alias: Door open to Telegram
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

Climate sensors:

```yaml
automation:
  - alias: Send room climate to Server Tg Home
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

## Telegram Commands

The bot registers Telegram commands, so the `/` menu shows them.

- `/start`: show `chat_id`, `user_id`, `message_thread_id`.
- `/help`: command list.
- `/cameras`: camera and buffer status.
- `/clip entrance 20`: record and send a 20 second clip.
- `/last entrance`: send the latest saved clip.
- `/snapshot entrance`: capture one frame.
- `/arm`: enable automatic event notifications.
- `/disarm`: disable automatic event notifications.
- `/mute 1h`: mute notifications temporarily.
- `/mute off`: clear mute.
- `/temp`: temperature and humidity.
- `/humidity`: humidity.
- `/analytics all 24h`: min/avg/max/latest sensor values.
- `/graph bedroom 24h`: bedroom graph for 24 hours.
- `/graph all 7d`: all rooms for 7 days.
- `/graph living_room 24h humidity`: living room humidity only.
- `/disk`: storage status.
- `/panel door`: send an inline-button panel.
- `/panel all`: send all configured panels.
- `/ac_on climate.bedroom`: call `climate.turn_on` in Home Assistant.
- `/status`: Redis, queues, DB and file status.

Admin-only actions: `/clip`, `/last`, `/snapshot`, `/arm`, `/disarm`, `/mute`, `/ac_on`, `/panel`, door panel camera buttons and voice playback.

## Graphs

`/graph` creates a job in `graphs.queue_name`.
`graph-worker` reads `sensor_readings`, renders Plotly and sends:

- PNG preview.
- HTML document with interactive Plotly graph.

Examples:

```text
/graph bedroom 24h
/graph all 7d
/graph living_room 24h humidity
/graph all 30d temperature
```

## Ubuntu Server Deployment

Recommended mini PC deployment: keep the repository on the server and build Docker images locally with `docker compose build`.

### 1. Prepare The Server

The setup script can install Docker Engine and Compose plugin automatically.
If you install base packages manually:

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates openssh-client
```

### 2. GitHub Deploy Key

For a private repository:

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

Add the public key to GitHub repository settings as a Deploy key.

If the repository is already available:

```bash
./scripts/deploy.sh ssh-key
```

### 3. Initial Install

```bash
sudo mkdir -p /opt/server-tg-home
sudo chown "$USER:$USER" /opt/server-tg-home
git clone git@github-servertghome:IvanOplesnin/ServerTgHome.git /opt/server-tg-home
cd /opt/server-tg-home

./scripts/deploy.sh init
```

`init` installs dependencies, creates `.env`, `config/config.yaml`, `config/go2rtc.yaml` and `data/`.
If runtime files were created for the first time, the stack is not started automatically.

Edit:

```bash
nano .env
nano config/config.yaml
nano config/go2rtc.yaml
```

Then:

```bash
./scripts/deploy.sh doctor
./scripts/deploy.sh deploy
```

### 4. Checks

```bash
./scripts/deploy.sh status
curl http://127.0.0.1:8080/health
docker compose logs -f --tail=200
```

`doctor` checks OS, architecture, required tools, Docker daemon access, git state, runtime files, compose config, service status and disk usage.

### 5. Updates

```bash
cd /opt/server-tg-home
./scripts/deploy.sh deploy
```

The script runs `git fetch`, `git pull --ff-only`, rebuilds application images when needed, runs `docker compose up -d --remove-orphans` and checks `/health`.

Update base images too:

```bash
STH_PULL_IMAGES=1 ./scripts/deploy.sh deploy
```

Force rebuild:

```bash
STH_FORCE=1 ./scripts/deploy.sh deploy
```

### 6. Systemd Timer

```bash
cd /opt/server-tg-home
./scripts/deploy.sh install-timer
```

Default interval is 10 minutes. Override:

```bash
STH_UPDATE_INTERVAL=30min ./scripts/deploy.sh install-timer
```

Remove:

```bash
./scripts/deploy.sh uninstall-timer
```

### 7. Operations

```bash
./scripts/deploy.sh doctor
./scripts/deploy.sh status
./scripts/deploy.sh logs
./scripts/deploy.sh restart
```

### 8. Backup And Restore

Backup:

```bash
./scripts/backup.sh
```

Backup includes Postgres dump, `.env`, `config/config.yaml` and `config/go2rtc.yaml` when present.
Videos are excluded by default. Include `data/` with:

```bash
STH_BACKUP_INCLUDE_DATA=1 ./scripts/backup.sh
```

Restore:

```bash
./scripts/restore.sh backups/server-tg-home-YYYYMMDD-HHMMSS.tar.gz
```

Current `.env`, `config/config.yaml`, `config/go2rtc.yaml` and `data` are saved first with a `restore-before-*` suffix.

## Operations And Troubleshooting

```bash
docker compose ps
docker compose logs -f api
docker compose logs -f worker buffer
docker compose logs -f audio-worker go2rtc
```

Check go2rtc RTSP from inside Docker:

```bash
docker compose exec worker ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1 rtsp://go2rtc:8554/living
```

Common issues:

- `401 Invalid webhook token`: `STH_WEBHOOK_TOKEN` and `X-Webhook-Token` do not match.
- Telegram does not respond: check `TELEGRAM_BOT_TOKEN`, `TELEGRAM_PROXY_URL` and proxy access.
- Tapo video fails during voice playback: use `ffmpeg_url: rtsp://go2rtc:8554/<stream>`.
- Voice playback reports success but no sound: check `go2rtc_restart_before_playback`, `preload` with `microphone=all`, and third-party compatibility in Tapo settings.
- Gray snapshot: check fresh buffer segments or direct `ffmpeg_url` availability.
- Empty graphs: check that Home Assistant sends `/webhooks/temperatures` and that `sensor_readings` is being populated.

## Local Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
cp config/config.example.yaml config/config.yaml
cp config/go2rtc.example.yaml config/go2rtc.yaml
server-tg-home api --host 0.0.0.0 --port 8080
```

For non-Docker local development, point `app.database_url` and `app.redis_url` to reachable Postgres/Redis instances.

Checks:

```bash
python -m compileall server_tg_home alembic
docker compose config --quiet
git diff --check
```
