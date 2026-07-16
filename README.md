# Server Tg Home

Основная документация на русском языке. Английская версия: [README.en.md](README.en.md).

Локальный сервис для событий Home Assistant, записи RTSP-видео и отправки сообщений, фото и видео в Telegram.

## Архитектура

- `api`: FastAPI HTTP API для webhook-запросов Home Assistant и aiogram long polling для Telegram-бота.
- `worker`: Dramatiq worker; получает `job_id`, читает детали задачи из БД и выполняет работу.
- `graph-worker`: отдельный Dramatiq worker для построения Plotly-графиков.
- `audio-worker`: отдельный Dramatiq worker для последовательного воспроизведения голосовых сообщений на динамиках камер.
- `go2rtc`: локальный media-gateway для Tapo two-way audio и будущих камер с talkback.
- `buffer`: поддерживает короткий постоянный RTSP-буфер по каждой камере.
- `retention`: APScheduler-процесс, который следит за размером папки с видео, предупреждает через Telegram и удаляет старые ролики при переполнении.
- `redis`: брокер очереди Dramatiq.
- `postgres`: постоянное хранилище задач, статусов, истории видео и истории датчиков.
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
  graphs/         Построение графиков Plotly и экспорт PNG/HTML.
  audio/          Подготовка голосовых сообщений и отправка аудио в go2rtc.
  media/          ffmpeg-запись, RTSP-буфер и работа с файлами.
  telegram/       aiogram polling и клиент отправки сообщений в Telegram.
  workers/        Долгоживущие процессы: буфер камер и очистка хранилища.
  cli.py          Точка входа для Docker и локальных команд.
```

Правила добавления новой логики:

- HTTP-обработчики добавляются в `api/`.
- Telegram-команды добавляются в `telegram/polling.py`.
- Новые типы задач добавляются в `jobs/factory.py` и `jobs/processor.py`.
- Рендеринг графиков добавляется в `graphs/`.
- Подготовка и воспроизведение аудио добавляется в `audio/`.
- Прямые вызовы внешних сервисов добавляются в `integrations/`.
- Логика видео, буфера и файлов добавляется в `media/`.
- Долгоживущие циклы и планировщики добавляются в `workers/`.

## Первый запуск

```bash
cp config/config.example.yaml config/config.yaml
cp config/go2rtc.example.yaml config/go2rtc.yaml
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
Команда `/start` также показывает `User id`; добавьте нужных пользователей в `telegram.admin_user_ids`, если хотите ограничить опасные действия.

Если бот должен писать в конкретную тему группового чата, добавьте бота в супергруппу с включенными темами и отправьте `/start` прямо в нужной теме.
Бот покажет `Chat id` и `Topic message_thread_id`. Для отправки всех событий в эту тему укажите:

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

Для отдельного события тему можно переопределить:

```yaml
events:
  door_open:
    camera_id: "entrance"
    chat_ids:
      - -1001234567890
    message_thread_id: 123
```

Если `message_thread_id` не задан, бот отправляет сообщения в обычный чат или в общий раздел группы. Команды `/clip`, `/snapshot`, `/status`, отправленные из темы, отвечают в эту же тему автоматически.

Для быстрых кнопок в темах настройте панели. Это не системное `/`-меню Telegram, а обычное сообщение бота с inline-кнопками, которое удобно закрепить в нужной теме:

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

После настройки отправьте `/panel door`, `/panel climate` или `/panel all`. Панель `door` создает кнопки для 20-секундного видео и фото с камеры. Панель `climate` создает кнопки текущей температуры/влажности и графиков за 6ч, 12ч, 24ч, 7д и 30д.

Если `telegram.admin_user_ids` пустой, все участники разрешенного чата могут выполнять команды. Если список заполнен, команды `/clip`, `/last`, `/snapshot`, `/arm`, `/disarm`, `/mute`, `/ac_on`, `/panel` и кнопки камеры в панели `door` доступны только этим пользователям.

Голосовые сообщения для воспроизведения на камерах требуют явного списка `telegram.admin_user_ids`: если список пустой, voice playback запрещен. Привяжите тему Telegram к камере:

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

Скопируйте `config/go2rtc.example.yaml` в `config/go2rtc.yaml` и настройте поток go2rtc. Для Tapo C200/C210 поток лучше задавать списком: `tapo://...` нужен для двустороннего звука, RTSP нужен для обычного видео/аудио. В `preload` оставьте `microphone=all`, чтобы go2rtc заранее держал talkback-подключение. Для таких камер `ffmpeg_url` лучше направлять на `rtsp://go2rtc:8554/<stream>`, чтобы буфер, клипы и snapshot не создавали лишние прямые RTSP-сессии к камере. Реальный `config/go2rtc.yaml` игнорируется git, потому что содержит пароль Tapo.

Когда админ отправляет voice message в привязанную тему, бот сохраняет исходный OGG/Opus в `audio.path`, создает `play_camera_audio` job, `audio-worker` конвертирует файл в `PCMA/8000 mono` и передает его в go2rtc. `audio-worker` запущен одним процессом и одним потоком, поэтому сообщения воспроизводятся строго по очереди. Старые аудиофайлы очищаются retention worker по `audio.retention_days`.

Перед каждым воспроизведением `audio-worker` по умолчанию делает `/api/restart` go2rtc и ждет, пока у потока появится talkback-подключение `audio sendonly`. Это защищает от ситуации, когда камера перезагрузилась, go2rtc держит устаревшее соединение и HTTP-запрос playback возвращает успех, но звук фактически не воспроизводится. Поведение настраивается параметрами `audio.go2rtc_restart_before_playback`, `audio.go2rtc_restart_wait_sec` и `audio.go2rtc_restart_poll_sec`.

Запуск:

```bash
docker compose up --build
```

## Деплой на Ubuntu Server

Для мини-ПК лучший вариант сейчас: хранить код в git на сервере и собирать Docker-образы локально через `docker compose build`.
Отдельный Docker registry пока не нужен, потому что проект небольшой и уже содержит `Dockerfile`/`docker-compose.yml`.

Если репозиторий приватный, сначала создайте на сервере SSH deploy key:

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

Если `scripts/deploy.sh` уже доступен на сервере, то же самое можно сделать командой `./scripts/deploy.sh ssh-key`.

Добавьте выведенный public key в GitHub repository settings как Deploy key. Для обновлений достаточно read-only доступа.

Первичная установка:

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

`init` установит Docker Engine и Docker Compose plugin, если они отсутствуют, создаст `.env`, `config/config.yaml` и `config/go2rtc.yaml`.
Если эти файлы были созданы впервые, сервис не стартует автоматически: сначала нужно заполнить Telegram token, RTSP URL, chat ids и остальные настройки.

Ручное обновление:

```bash
cd /opt/server-tg-home
./scripts/deploy.sh deploy
```

Скрипт делает `git pull --ff-only`, пересобирает приложение при изменениях и запускает `docker compose up -d`.
Чтобы дополнительно проверить обновления базовых Docker-образов `postgres`, `redis` и `go2rtc`, запустите `STH_PULL_IMAGES=1 ./scripts/deploy.sh deploy`.

Автоматическая проверка обновлений через systemd timer:

```bash
cd /opt/server-tg-home
./scripts/deploy.sh install-timer
```

Timer по умолчанию проверяет обновления каждые 10 минут. Устанавливайте его от того же пользователя, у которого настроен SSH-ключ к GitHub.

Полезные команды:

```bash
./scripts/deploy.sh status
./scripts/deploy.sh logs
./scripts/deploy.sh restart
./scripts/deploy.sh uninstall-timer
```

Резервная копия:

```bash
./scripts/backup.sh
```

По умолчанию в backup попадают Postgres, `.env`, `config/config.yaml` и `config/go2rtc.yaml`, если он есть. Видео не включаются, чтобы архив не стал слишком большим.
Если нужно сохранить и папку `data`, запустите:

```bash
STH_BACKUP_INCLUDE_DATA=1 ./scripts/backup.sh
```

Восстановление:

```bash
./scripts/restore.sh backups/server-tg-home-YYYYMMDD-HHMMSS.tar.gz
```

Перед восстановлением текущие `.env`, `config/config.yaml`, `config/go2rtc.yaml` и `data` сохраняются рядом с суффиксом `restore-before-*`.

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

Температуру и влажность можно обновлять webhook-запросом. Сервис сохраняет последнее значение в Postgres, а команда `/temp` показывает последние данные.
Старый payload только с `temperatures` продолжает работать; `humidities` можно добавлять позже без изменения старого контракта.
Эти же webhook-запросы пишут историю в таблицу `sensor_readings`; она используется для графиков.

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

## Команды Telegram

Бот автоматически регистрирует меню команд Telegram, поэтому при вводе `/` клиент показывает доступные команды.

- `/start`: показывает текущий `chat_id`.
- `/cameras`: показывает состояние камер и буфера.
- `/clip entrance 20`: записывает и отправляет 20-секундный клип.
- `/last entrance`: отправляет последний сохраненный ролик по камере.
- `/snapshot entrance`: делает и отправляет один кадр с камеры.
- `/arm`: включает автоматические уведомления по событиям.
- `/disarm`: выключает автоматические уведомления по событиям.
- `/mute 1h`: временно отключает автоматические уведомления, `/mute off` снимает mute.
- `/temp`: показывает температуру и влажность в спальне и гостиной.
- `/humidity`: показывает влажность в спальне и гостиной.
- `/analytics all 24h`: показывает минимум, среднее, максимум и последнее значение датчиков за период.
- `/graph bedroom 24h`: строит график температуры и влажности по спальне за 24 часа.
- `/graph all 7d`: строит общий график по всем комнатам за 7 дней.
- `/graph living_room 24h humidity`: строит только влажность по гостиной.
- `/disk`: показывает состояние папок видео, буфера и графиков.
- `/panel door`: отправляет в тему сообщение-панель с inline-кнопками.
- `/panel all`: отправляет все настроенные панели.
- `/ac_on climate.bedroom`: вызывает `climate.turn_on` в Home Assistant.
- `/status`: показывает статус Redis, очереди, БД и хранилища.

Команда `/graph` создает задачу в отдельной очереди `graphs.queue_name`. `graph-worker` читает историю из `sensor_readings`, строит Plotly-график и отправляет в Telegram:

- PNG-превью для быстрого просмотра в чате.
- HTML-файл с интерактивным Plotly-графиком для zoom/hover/выключения серий.

Артефакты сохраняются в `graphs.path`, по умолчанию `/data/graphs`. Старые HTML/PNG-файлы и история датчиков очищаются retention worker по настройкам `graphs.artifact_retention_days` и `graphs.history_retention_days`.

## Healthcheck Камер

`retention` дополнительно проверяет здоровье камер. Основной сигнал - свежесть сегментов RTSP-буфера. Если камера перестала писать новые сегменты, сервис отправит уведомление в Telegram; при восстановлении может отправить отдельное уведомление.

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

Если `stale_after_sec` не задан, порог берется из настроек буфера: максимум из `buffer.keep_seconds`, `buffer.segment_seconds * 3` и 30 секунд. `startup_grace_sec` нужен, чтобы после перезапуска контейнеров не получить ложный алерт до появления первых сегментов.

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

`cooldown_sec` не дает одному событию создавать новые задачи чаще заданного интервала.
`dedupe_window_sec` отбрасывает повторный webhook с тем же payload внутри короткого окна.

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
