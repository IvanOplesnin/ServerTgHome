# Server Tg Home

English documentation. Main documentation in Russian: [README.md](README.md).

Local service for Home Assistant events, RTSP camera recording and Telegram message, photo and video delivery.

## Architecture

- `api`: FastAPI HTTP API for Home Assistant webhooks plus aiogram long polling for the Telegram bot.
- `worker`: Dramatiq worker; receives `job_id`, loads job details from the database and performs the work.
- `graph-worker`: dedicated Dramatiq worker for Plotly graph rendering.
- `audio-worker`: dedicated Dramatiq worker for sequential voice playback on camera speakers.
- `go2rtc`: local media gateway for Tapo two-way audio and future talkback-capable cameras.
- `buffer`: keeps a short rolling RTSP buffer for each camera.
- `retention`: APScheduler process that monitors the clip folder size, warns via Telegram and deletes old clips when the limit is reached.
- `redis`: Dramatiq queue broker.
- `postgres`: persistent job, status, video history and sensor history database.
- `alembic`: database schema migrations.

The queue stores only `job_id`. Job payload, status, attempts and history live in Postgres.

## Project Structure

```text
server_tg_home/
  api/            FastAPI app, HTTP request models and routes.
  core/           Settings, logging and shared status rendering.
  database/       SQLAlchemy session, ORM models and Alembic migration runner.
  integrations/   External systems except Telegram: Home Assistant and future APIs.
  jobs/           Job creation, DB status transitions, Dramatiq queue and actors.
  graphs/         Plotly graph rendering and PNG/HTML export.
  audio/          Voice message preparation and audio delivery to go2rtc.
  media/          ffmpeg recording, RTSP buffer segment handling and file storage.
  telegram/       aiogram polling and Telegram send client.
  workers/        Long-running processes: camera buffer and retention.
  cli.py          Process entrypoint used by Docker and local commands.
```

Rules for adding new logic:

- HTTP endpoints belong in `api/`.
- Telegram commands belong in `telegram/polling.py`.
- New job types belong in `jobs/factory.py` and `jobs/processor.py`.
- Graph rendering belongs in `graphs/`.
- Audio preparation and playback belongs in `audio/`.
- Direct calls to external services belong in `integrations/`.
- Video, buffer and file logic belongs in `media/`.
- Long-running loops and schedulers belong in `workers/`.

## First Run

```bash
cp config/config.example.yaml config/config.yaml
cp config/go2rtc.example.yaml config/go2rtc.yaml
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
`/start` also shows `User id`; add trusted users to `telegram.admin_user_ids` if you want to restrict dangerous actions.

If the bot should post to a specific group topic, add it to a supergroup with topics enabled and send `/start` in the target topic.
The bot will show `Chat id` and `Topic message_thread_id`. To send all default events to that topic, set:

```yaml
telegram:
  allowed_chat_ids:
    - -1001234567890
  default_chat_ids:
    - -1001234567890
  default_message_thread_id: 123
  admin_user_ids:
    - 123456789
```

You can override the topic for a specific event:

```yaml
events:
  door_open:
    camera_id: "entrance"
    chat_ids:
      - -1001234567890
    message_thread_id: 123
```

When `message_thread_id` is not set, the bot sends messages to a normal chat or the group's general topic. `/clip`, `/snapshot` and `/status` commands sent from a topic reply to the same topic automatically.

For quick topic buttons, configure panels. This is not Telegram's system `/` command menu; it is a regular bot message with inline buttons that can be pinned in the target topic:

```yaml
telegram:
  panels:
    door:
      title: "Entrance Door"
      kind: "door"
      chat_id: -1001234567890
      message_thread_id: 10
      camera_id: "entrance"
      video_duration_sec: 20

    climate:
      title: "Temperature and Humidity"
      kind: "climate"
      chat_id: -1001234567890
      message_thread_id: 20
      room_id: "all"
```

After configuring panels, send `/panel door`, `/panel climate` or `/panel all`. The `door` panel creates buttons for a 20 second video and a snapshot. The `climate` panel creates buttons for current temperature/humidity and graphs for 6h, 12h, 24h, 7d and 30d.

When `telegram.admin_user_ids` is empty, every allowed chat member can use commands. When it is set, `/clip`, `/last`, `/snapshot`, `/arm`, `/disarm`, `/mute`, `/ac_on`, `/panel` and camera buttons in the `door` panel are admin-only.

Voice messages for camera speaker playback require an explicit `telegram.admin_user_ids` list: when the list is empty, voice playback is disabled. Map each Telegram topic to a camera:

```yaml
telegram:
  camera_topics:
    living:
      chat_id: -1001234567890
      message_thread_id: 30
      camera_id: "living"

cameras:
  living:
    rtsp_url: "rtsp://user:password@192.168.1.26:554/stream1"
    ffmpeg_url: "rtsp://go2rtc:8554/living"
    buffer_enabled: true
    speaker_enabled: true
    go2rtc_stream: "living"
    speaker_audio_codec: "pcma"
```

Copy `config/go2rtc.example.yaml` to `config/go2rtc.yaml` and configure the go2rtc stream. For Tapo C200/C210, define the stream as a list: `tapo://...` is required for two-way audio, while RTSP is used for regular video/audio. Keep `microphone=all` in `preload` so go2rtc keeps the talkback connection ready. For these cameras, point `ffmpeg_url` to `rtsp://go2rtc:8554/<stream>` so the buffer, clips and snapshots do not open extra direct RTSP sessions to the camera. The real `config/go2rtc.yaml` is ignored by git because it contains the Tapo password.

When an admin sends a voice message in a mapped topic, the bot stores the original OGG/Opus file under `audio.path`, creates a `play_camera_audio` job, `audio-worker` converts it to `PCMA/8000 mono` and sends it to go2rtc. `audio-worker` runs with one process and one thread, so messages are played strictly in queue order. Old audio files are cleaned by the retention worker using `audio.retention_days`.

Before every playback, `audio-worker` runs go2rtc `/api/restart` by default and waits until the stream exposes a talkback `audio sendonly` producer. This avoids the case where a camera reboot leaves go2rtc with a stale connection: the playback HTTP request can return success while no sound is actually played. The behavior is controlled by `audio.go2rtc_restart_before_playback`, `audio.go2rtc_restart_wait_sec` and `audio.go2rtc_restart_poll_sec`.

When `audio.reaction_clip_enabled` is enabled, `audio-worker` starts a separate video recording through `ffmpeg_url` before actual playback, waits `audio.reaction_pre_event_sec` seconds, plays the voice message, records for another `audio.reaction_post_event_sec` seconds, and sends the reaction clip back to the same Telegram topic. This clip does not depend on the rolling buffer and is stored in the regular video storage, so the existing retention mechanism cleans it up.

Start:

```bash
docker compose up --build
```

## Ubuntu Server Deployment

For a mini PC, the best option for now is git-based deployment: keep the repository on the server and build Docker images locally with `docker compose build`.
A separate Docker registry is not needed yet because the project is small and already includes `Dockerfile`/`docker-compose.yml`.

If the repository is private, create an SSH deploy key on the server first:

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

If `scripts/deploy.sh` is already available on the server, you can do the same with `./scripts/deploy.sh ssh-key`.

Add the printed public key to the GitHub repository settings as a Deploy key. Read-only access is enough for updates.

Initial setup:

```bash
sudo mkdir -p /opt/server-tg-home
sudo chown "$USER:$USER" /opt/server-tg-home
git clone git@github-servertghome:IvanOplesnin/ServerTgHome.git /opt/server-tg-home
cd /opt/server-tg-home

./scripts/deploy.sh init
nano .env
nano config/config.yaml
nano config/go2rtc.yaml
./scripts/deploy.sh deploy
```

`init` installs Docker Engine and the Docker Compose plugin if they are missing, then creates `.env`, `config/config.yaml` and `config/go2rtc.yaml`.
If those files were created for the first time, the stack is not started automatically: fill Telegram token, RTSP URL, chat ids and the rest of the settings first.

Manual update:

```bash
cd /opt/server-tg-home
./scripts/deploy.sh deploy
```

The script runs `git pull --ff-only`, rebuilds the application when code changed, and runs `docker compose up -d`.
To also check base Docker image updates for `postgres`, `redis` and `go2rtc`, run `STH_PULL_IMAGES=1 ./scripts/deploy.sh deploy`.

Automatic update checks with a systemd timer:

```bash
cd /opt/server-tg-home
./scripts/deploy.sh install-timer
```

The timer checks for updates every 10 minutes by default. Install it as the same user that owns the GitHub SSH key.

Useful commands:

```bash
./scripts/deploy.sh status
./scripts/deploy.sh logs
./scripts/deploy.sh restart
./scripts/deploy.sh uninstall-timer
```

Backup:

```bash
./scripts/backup.sh
```

By default, the backup includes Postgres, `.env`, `config/config.yaml` and `config/go2rtc.yaml` when it exists. Videos are not included so the archive does not become too large.
To include the `data` directory too, run:

```bash
STH_BACKUP_INCLUDE_DATA=1 ./scripts/backup.sh
```

Restore:

```bash
./scripts/restore.sh backups/server-tg-home-YYYYMMDD-HHMMSS.tar.gz
```

Before restoring, current `.env`, `config/config.yaml`, `config/go2rtc.yaml` and `data` are saved next to them with a `restore-before-*` suffix.

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

Room temperature and humidity can be updated with a webhook request. The service stores the latest values in Postgres and `/temp` shows the last known data.
The old `temperatures`-only payload still works; `humidities` can be added later without changing that contract.
The same webhooks also write history to the `sensor_readings` table; graphs are built from that history.

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
    url: "http://server-host:8080/webhooks/temperatures"
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

The bot registers the Telegram command menu automatically, so typing `/` shows the available commands in the client.

- `/start`: shows the current `chat_id`.
- `/cameras`: shows camera and buffer status.
- `/clip entrance 20`: records and sends a 20 second clip.
- `/last entrance`: sends the latest saved video for a camera.
- `/snapshot entrance`: captures and sends one camera frame.
- `/arm`: enables automatic event notifications.
- `/disarm`: disables automatic event notifications.
- `/mute 1h`: temporarily disables automatic event notifications, `/mute off` clears the mute.
- `/temp`: shows bedroom and living room temperature and humidity.
- `/humidity`: shows bedroom and living room humidity.
- `/analytics all 24h`: shows min, average, max and latest sensor values for the selected period.
- `/graph bedroom 24h`: builds a temperature and humidity graph for the bedroom over 24 hours.
- `/graph all 7d`: builds a graph for all rooms over 7 days.
- `/graph living_room 24h humidity`: builds only living room humidity.
- `/disk`: shows clip, buffer and graph folder usage.
- `/panel door`: sends a topic panel message with inline buttons.
- `/panel all`: sends all configured panels.
- `/ac_on climate.bedroom`: calls `climate.turn_on` in Home Assistant.
- `/status`: shows Redis, queue, database and storage status.

`/graph` creates a job in the dedicated `graphs.queue_name` queue. `graph-worker` reads `sensor_readings`, renders a Plotly graph and sends to Telegram:

- PNG preview for quick viewing in chat.
- HTML file with an interactive Plotly graph for zoom/hover/toggling series.

Artifacts are saved under `graphs.path`, `/data/graphs` by default. Old HTML/PNG files and sensor history are cleaned by the retention worker using `graphs.artifact_retention_days` and `graphs.history_retention_days`.

## Camera Healthcheck

`retention` also checks camera health. The primary signal is RTSP buffer freshness. If a camera stops writing new segments, the service sends a Telegram notification; it can also notify when the camera recovers.

```yaml
camera_health:
  enabled: true
  poll_sec: 60
  stale_after_sec: null
  startup_grace_sec: 120
  notify_recovery: true
  notify_chat_ids:
    - -1001234567890
  notify_message_thread_id: 123
```

When `stale_after_sec` is not set, the threshold is derived from the buffer settings: the maximum of `buffer.keep_seconds`, `buffer.segment_seconds * 3` and 30 seconds. `startup_grace_sec` avoids false alerts immediately after container restarts before the first buffer segments appear.

## Multiple Cameras

Add cameras under `cameras`; events and Telegram commands should refer to their ids:

```yaml
cameras:
  entrance:
    rtsp_url: "rtsp://user:password@192.168.1.10:554/stream1"
    buffer_enabled: true
    default_duration_sec: 20

  yard:
    rtsp_url: "rtsp://user:password@192.168.1.11:554/stream1"
    ffmpeg_url: null
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

  yard_motion:
    camera_id: "yard"
    duration_sec: 20
    pre_event_sec: 4
    cooldown_sec: 30
    dedupe_window_sec: 5
    message: "Yard motion"
```

`cooldown_sec` prevents the same event from creating new jobs more often than the configured interval.
`dedupe_window_sec` drops repeated webhooks with the same payload inside a short window.

You do not need one `buffer` container per camera. One `buffer` process starts one `ffmpeg` process per camera with `buffer_enabled: true`.

Temporary buffer segments are stored under:

```text
<buffer.path>/<camera_id>/
```

With the default config this means:

```text
/data/buffer/entrance/
/data/buffer/yard/
```

`ffmpeg_output_args` is used for the continuous buffer. In most cases keep `-c:v copy` there so the camera stream is not transcoded constantly.
`ffmpeg_clip_output_args` is used only for the final Telegram clip; by default it transcodes to H.264/AAC so Telegram handles duration and audio reliably.

Usually you scale `worker` when the number of heavy jobs grows. Keep `buffer` as a single instance unless cameras are split across different physical servers or network/CPU load becomes too high.

## Local Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
cp config/config.example.yaml config/config.yaml
server-tg-home api --host 0.0.0.0 --port 8080
```

For local development outside Docker, set `app.database_url` in `config/config.yaml` to a reachable Postgres instance.
