# Server Tg Home

English documentation. Main documentation in Russian: [README.md](README.md).

Local service for Home Assistant events, RTSP camera recording and Telegram message, photo and video delivery.

## Architecture

- `api`: FastAPI HTTP API for Home Assistant webhooks plus aiogram long polling for the Telegram bot.
- `worker`: Dramatiq worker; receives `job_id`, loads job details from the database and performs the work.
- `buffer`: keeps a short rolling RTSP buffer for each camera.
- `retention`: APScheduler process that monitors the clip folder size, warns via Telegram and deletes old clips when the limit is reached.
- `redis`: Dramatiq queue broker.
- `postgres`: persistent job, status and video history database.
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
  media/          ffmpeg recording, RTSP buffer segment handling and file storage.
  telegram/       aiogram polling and Telegram send client.
  workers/        Long-running processes: camera buffer and retention.
  cli.py          Process entrypoint used by Docker and local commands.
```

Rules for adding new logic:

- HTTP endpoints belong in `api/`.
- Telegram commands belong in `telegram/polling.py`.
- New job types belong in `jobs/factory.py` and `jobs/processor.py`.
- Direct calls to external services belong in `integrations/`.
- Video, buffer and file logic belongs in `media/`.
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

If the bot should post to a specific group topic, add it to a supergroup with topics enabled and send `/start` in the target topic.
The bot will show `Chat id` and `Topic message_thread_id`. To send all default events to that topic, set:

```yaml
telegram:
  allowed_chat_ids:
    - -1001234567890
  default_chat_ids:
    - -1001234567890
  default_message_thread_id: 123
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
./scripts/deploy.sh deploy
```

`init` installs Docker Engine and the Docker Compose plugin if they are missing, then creates `.env` and `config/config.yaml`.
If those files were created for the first time, the stack is not started automatically: fill Telegram token, RTSP URL, chat ids and the rest of the settings first.

Manual update:

```bash
cd /opt/server-tg-home
./scripts/deploy.sh deploy
```

The script runs `git pull --ff-only`, rebuilds the application when code changed, and runs `docker compose up -d`.
To also check base Docker image updates for `postgres` and `redis`, run `STH_PULL_IMAGES=1 ./scripts/deploy.sh deploy`.

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

The bot registers the Telegram command menu automatically, so typing `/` shows the available commands in the client.

- `/start`: shows the current `chat_id`.
- `/cameras`: shows camera and buffer status.
- `/clip entrance 20`: records and sends a 20 second clip.
- `/last entrance`: sends the latest saved video for a camera.
- `/snapshot entrance`: captures and sends one camera frame.
- `/arm`: enables automatic event notifications.
- `/disarm`: disables automatic event notifications.
- `/mute 1h`: temporarily disables automatic event notifications, `/mute off` clears the mute.
- `/ac_on climate.bedroom`: calls `climate.turn_on` in Home Assistant.
- `/status`: shows Redis, queue, database and storage status.

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
