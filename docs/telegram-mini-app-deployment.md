# Публикация Telegram Mini App через Caddy на Raspberry Pi

## Итоговая схема

В проекте не запускается собственный Caddy и не публикуются `80/443`.
Единственной публичной HTTPS-точкой остается существующий Caddy на Raspberry Pi
(`ssh raps`). Контейнер `miniapp-web` на mini PC только собирает и отдает
статический React frontend через непривилегированный Nginx.

```text
Telegram WebView
    |
    | HTTPS :443
    v
Caddy на raps
    |-- /                         -> mini PC :18082 (miniapp-web)
    |-- /api/webapp/*             -> mini PC :28080 (FastAPI)
    `-- разрешенные /media/*       -> mini PC :21984 (go2rtc)
             |
             `-- forward_auth     -> mini PC :28080

WebRTC media plane
Telegram WebView <---------- TCP/UDP :8555 ----------> go2rtc на mini PC
```

Проверка инфраструктуры до применения этой ветки показала:

- Caddy на `raps` уже запущен в Docker и занимает `80/443`;
- он уже используется как центральный reverse proxy для сервисов mini PC;
- FastAPI на `18080` доступен из контейнера Caddy;
- go2rtc на `1984` пока привязан к loopback mini PC и недоступен с `raps`.

## Порты

На роутере:

| Порт | Куда направить | Назначение |
|---|---|---|
| `80/tcp` | Raspberry Pi | ACME и перенаправление на HTTPS |
| `443/tcp` | Raspberry Pi | Mini App, API, MSE/HLS и WebSocket signaling |
| `8555/tcp+udp` | mini PC | прямой WebRTC media transport go2rtc |

Публичная DNS `A`-запись домена должна указывать на статический внешний IP.
Если роутер не поддерживает NAT loopback, настройте split DNS: внутри домашней
сети тот же домен должен разрешаться в LAN-адрес Raspberry Pi. Публичный
WebRTC candidate при этом по-прежнему направляется через `8555` на mini PC.

Только между доверенным Caddy и mini PC:

| Порт | Сервис |
|---|---|
| `18082/tcp` | статический `miniapp-web` |
| `28080/tcp` | trusted proxy port FastAPI |
| `21984/tcp` | trusted proxy port go2rtc HTTP/WebSocket API |

Порты `18082`, `28080` и `21984` нельзя пробрасывать на интернет. Привяжите их
к LAN/VPN-адресу mini PC и ограничьте firewall так, чтобы подключения
принимались только от адреса Raspberry Pi. Локальные порты `18080` и `1984`
по-прежнему публикуются только на `127.0.0.1` для Home Assistant и диагностики.
Для Docker-портов firewall-правило должно применяться до Docker forwarding,
например в `DOCKER-USER`/nftables; одной привязки к LAN интерфейсу недостаточно
для изоляции от остальных устройств локальной сети.

## Runtime-настройка mini PC

В реальном `.env` mini PC:

```dotenv
# LAN/VPN-адрес mini PC, доступный с raps. Не использовать 0.0.0.0.
STH_REVERSE_PROXY_BIND_ADDRESS=<MINIPC_LAN_IP>
COMPOSE_PROFILES=miniapp
```

Если reverse proxy находится на том же хосте, оставьте безопасное значение
`127.0.0.1`. `STH_REVERSE_PROXY_BIND_ADDRESS` используется только для
`18082`, `28080` и `21984`; локальные `18080/1984` и публичный WebRTC
`8555/tcp+udp` настраиваются отдельно.

В `config/config.yaml`:

```yaml
telegram:
  admin_user_ids:
    - 111111111

webapp:
  enabled: true
  public_url: "https://miniapp.example.com"
  primary_chat_id: -1001234567890
  viewer_user_ids:
    - 222222222
  require_group_membership: true
  membership_recheck_sec: 300
```

Администраторы берутся из существующего `telegram.admin_user_ids`.
Наблюдатель должен одновременно находиться в `viewer_user_ids` и оставаться
участником `primary_chat_id`. Пустой allowlist доступ не открывает. Для надежной
проверки `getChatMember` бот должен быть администратором основной группы.

В runtime `config/go2rtc.yaml` замените TEST-NET адрес из example-файла на
статический публичный IPv4:

```yaml
webrtc:
  listen: ":8555"
  candidates:
    - 203.0.113.10:8555  # пример; заменить только в runtime-конфиге
```

Камеры `entrance`, `living` и `bed` должны существовать одновременно в
`config/config.yaml` и `config/go2rtc.yaml`; `go2rtc_stream` должен совпадать с
именем соответствующего stream.

Если основной поток камеры использует HEVC/H.265 или разрешение выше 1080p,
добавьте отдельный H.264-вариант для Mini App. go2rtc запускает его по
требованию, только пока открыт просмотр:

```yaml
# config/go2rtc.yaml
streams:
  entrance:
    - rtsp://CAMERA_ACCOUNT:CAMERA_PASSWORD@CAMERA_LAN_IP:554/stream1
  entrance_web:
    - "ffmpeg:entrance#video=h264#width=1920#height=-2#audio=aac"
```

```yaml
# config/config.yaml
cameras:
  entrance:
    go2rtc_stream: "entrance_web"
```

Финальные MP4 для архива должны быть не больше 1920×1080 и использовать H.264
High Level 4.1 с частотой не выше 30 кадров/с, `yuv420p`, AAC 48 kHz и
`+faststart`. Эти параметры задаются через `ffmpeg_clip_output_args`;
постоянный pre-event buffer по-прежнему может копировать исходный поток без
круглосуточного транскодирования.

На mini PC используется отдельный runtime `compose.yaml`. В него нужно перенести
из репозиторного `docker-compose.yml`:

- сервис `miniapp-web`;
- отдельные trusted proxy mappings `28080:8080` и `21984:1984` с
  `STH_REVERSE_PROXY_BIND_ADDRESS`;
- заменить прежний `18080:8080` или `0.0.0.0:18080:8080` на
  `127.0.0.1:18080:8080`, иначе полный API останется доступен всей LAN;
- оставить локальный go2rtc mapping только как `127.0.0.1:1984:1984`;
- публикацию `8555/tcp+udp`;
- `--no-access-log` для API.

Целевой фрагмент port mappings:

```yaml
go2rtc:
  ports:
    - "127.0.0.1:1984:1984"
    - "${STH_REVERSE_PROXY_BIND_ADDRESS}:21984:1984"
    - "8555:8555/tcp"
    - "8555:8555/udp"

api:
  ports:
    - "127.0.0.1:18080:8080"
    - "${STH_REVERSE_PROXY_BIND_ADDRESS}:28080:8080"

miniapp-web:
  ports:
    - "${STH_REVERSE_PROXY_BIND_ADDRESS}:18082:8080"
```

После изменения:

```bash
docker compose --profile miniapp config --quiet
docker compose --profile miniapp up -d --build --force-recreate \
  miniapp-web api go2rtc buffer
```

## Миграция старых записей

Изменение `ffmpeg_clip_output_args` влияет только на новые файлы. Старые MP4 с
высоким H.264 Level, разрешением выше 1080p или аудио 8 kHz можно безопасно
привести к совместимому формату встроенной утилитой. Сначала остановите
retention и выполните dry-run:

```bash
docker compose stop retention
docker compose run --rm --no-deps worker \
  python -m server_tg_home.tools.recording_compat \
  --camera entrance \
  --backup-dir /data/migration-backups/entrance-webcompat-YYYYMMDDTHHMMSSZ \
  --dry-run
```

Если dry-run не показывает ошибок, повторите ту же команду с `--apply`.
Утилита проверяет, что каждый файл находится внутри `storage.path`, создаёт
hardlink оригинала на том же диске, кодирует во временный файл, валидирует
codec, размеры, FPS, аудио, длительность и `faststart`, затем атомарно заменяет
оригинал и обновляет `videos.size_bytes`. Повторный запуск безопасен: уже
совместимые записи пропускаются. После проверки результата верните retention:

```bash
docker compose up -d retention
```

Каталог backup не удаляйте до ручной проверки воспроизведения в Telegram.

## Маршруты для существующего Caddy

Ниже шаблон отдельного публичного site block для Caddy на `raps`.
Замените домен и `MINIPC_LAN_IP`. Не добавляйте используемый для внутренних
сервисов `import local_only`: Telegram должен открывать Mini App из внешней сети.

```caddyfile
miniapp.example.com {
	encode zstd gzip

	header {
		-Server
		Content-Security-Policy "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' data: blob:; connect-src 'self' wss://miniapp.example.com; font-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors https://telegram.org https://*.telegram.org"
		Permissions-Policy "camera=(), geolocation=(), microphone=()"
		Referrer-Policy "no-referrer"
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		X-Content-Type-Options "nosniff"
	}

	@immutable path /assets/*
	header @immutable Cache-Control "public, max-age=31536000, immutable"

	@internal_webapp_api path /api/webapp/v1/media/authorize /api/webapp/v1/media/authorize/*
	handle @internal_webapp_api {
		respond "Not Found" 404
	}

	@webapp_api path /api/webapp /api/webapp/*
	handle @webapp_api {
		reverse_proxy MINIPC_LAN_IP:28080
	}

	@blocked_api path /api /api/*
	handle @blocked_api {
		respond "Not Found" 404
	}

	@player_scripts {
		method GET HEAD
		path /media/video-stream.js /media/video-rtc.js
	}
	handle @player_scripts {
		uri strip_prefix /media
		header Cache-Control "public, max-age=86400"
		reverse_proxy MINIPC_LAN_IP:21984
	}

	@protected_media {
		method GET HEAD
		path_regexp protected_media ^/media/t/[A-Za-z0-9_-]{32,256}(?P<upstream>/api/(?:ws|stream[.]m3u8|hls/(?:playlist[.]m3u8|segment[.]ts|init[.]mp4|segment[.]m4s)))$
	}
	handle @protected_media {
		route {
			forward_auth MINIPC_LAN_IP:28080 {
				uri /api/webapp/v1/media/authorize
			}
			rewrite * {re.protected_media.upstream}
			header Cache-Control "no-store"
			reverse_proxy MINIPC_LAN_IP:21984 {
				# Browser Origin contains the public HTTPS domain, while go2rtc
				# sees this trusted hop as internal HTTP and otherwise returns 403.
				# forward_auth above has already validated the capability ticket.
				header_up -Origin
				stream_timeout 10m
			}
		}
	}

	@blocked_media path /media /media/*
	handle @blocked_media {
		respond "Not Found" 404
	}

	handle {
		reverse_proxy MINIPC_LAN_IP:18082
	}
}
```

Этот allowlist открывает только:

- два JavaScript-модуля плеера;
- `/api/ws`;
- `/api/stream.m3u8`;
- нужные playlist/segment endpoints HLS.

Остальной go2rtc API остается закрытым. Перед каждым новым media-соединением
Caddy обращается к FastAPI, который проверяет ticket, разрешенную камеру,
allowlist пользователя и членство в группе. Постоянный WebSocket ограничен
десятью минутами, а frontend обновляет ticket и переподключается заранее.
`Origin` удаляется только на этом уже авторизованном proxy-hop: это сохраняет
встроенную origin-защиту go2rtc на остальных его интерфейсах и не требует
небезопасного глобального `api.origin: "*"`.

Capability-ticket находится в URL. Не включайте access log для
`/media/t/*` и `/api/webapp/v1/files/*`. В проекте Uvicorn и `miniapp-web`
уже запускаются без access log.

## Безопасное обновление Caddy на raps

Перед ручным изменением сделайте резервную копию Caddyfile. Затем проверьте
конфигурацию внутри уже запущенного контейнера:

```bash
ssh raps
cd <CADDY_COMPOSE_DIR>
docker compose exec -T caddy \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose exec -T caddy \
  caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

Если менялся только bind-mounted Caddyfile, пересобирать образ не нужно.
Если менялись volumes или networks в compose Caddy, выполните
`docker compose up -d --force-recreate caddy`.

## Проверка

С mini PC:

```bash
curl -fsS http://127.0.0.1:18080/health
docker compose --profile miniapp ps
```

С Raspberry Pi нужно проверить три внутренних upstream без вывода ответов:

```bash
curl -fsS -o /dev/null http://MINIPC_LAN_IP:18082/healthz
curl -fsS -o /dev/null http://MINIPC_LAN_IP:28080/health
curl -fsS -o /dev/null http://MINIPC_LAN_IP:21984/
```

С внешнего клиента:

```bash
curl -I https://miniapp.example.com/
```

После этого зарегистрируйте точный HTTPS URL в BotFather. В групповом чате
команда `/app` публикует общую панель; панели камер и климата также получают
ссылку на Mini App.

## Нужен ли proxy

`TELEGRAM_PROXY_URL` относится только к исходящим запросам бота к Telegram API.
Если mini PC соединяется с Telegram напрямую, оставьте proxy пустым. Он не
публикует Mini App и не заменяет Caddy, DNS или port forwarding.

Обычный HTTP/SOCKS proxy также не передает WebRTC. Для прямого WebRTC на
роутере обязательны два отдельных правила: `8555/tcp` и `8555/udp` на mini PC.
Если этот порт недоступен, плеер пробует MSE/HLS через HTTPS `443`; TURN можно
добавить позже как отдельный media transport.

## Диагностика

- SPA не открывается: проверить `miniapp-web:18082`, site block и DNS.
- API отвечает `401/403`: проверить Telegram init data, allowlist и членство в
  основной группе.
- Caddy получает `502` на media: `21984` не привязан к доступному с `raps`
  интерфейсу либо заблокирован firewall.
- WebSocket получает `401/404`: проверить ticket и соответствие
  `go2rtc_stream`.
- Архив скачивается, но не проигрывается: проверить `ffprobe` — MP4 должен
  содержать H.264 не выше Level 4.1, `yuv420p`, разрешение не выше 1080p и AAC
  с частотой 44.1/48 kHz, а FPS не должен превышать 30. Старые несовместимые
  файлы нужно один раз перекодировать с резервной копией.
- Live открывается без изображения: проверить codec выбранного
  `go2rtc_stream`; HEVC/H.265 или поток выше 1080p следует отдавать через
  отдельный H.264 stream.
- WebRTC не соединяется: проверить оба протокола `8555`, public candidate и
  перенаправление порта на mini PC.
- Никогда не открывать `18082`, `28080` или `21984` в интернет.
