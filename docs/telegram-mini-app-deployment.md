# Публикация Telegram Mini App

Mini App публикуется через отдельный контейнер `miniapp-gateway`. Он собирает
React-приложение в образе `node:24-alpine`, а затем отдает статические файлы и
терминирует HTTPS через `caddy:2.11.4-alpine`.

```text
Telegram WebView
    |
    | HTTPS :443
    v
Caddy (miniapp-gateway)
    |-- /                         -> статическая SPA
    |-- /api/webapp/*             -> FastAPI
    `-- только разрешенные media  -> go2rtc :1984

WebRTC media plane
Telegram WebView <---------- TCP/UDP :8555 ----------> go2rtc
```

Публичный доступ к административному API go2rtc не нужен. Caddy пропускает
только два JavaScript-файла плеера и короткий список media-маршрутов, защищенных
короткоживущим ticket. Ticket можно повторно использовать в пределах его TTL:
это нужно для HTTP Range и сегментов HLS. Все остальные запросы `/media/*`
возвращают `404`.
Стандартный access log Caddy отключён, потому что capability-ticket находится
в URI; production-команда API также запускает Uvicorn с `--no-access-log`.
Operational-ошибки обоих сервисов по-прежнему попадают в логи контейнеров.
Из FastAPI наружу проксируется только префикс `/api/webapp/*`; webhook и
служебные endpoints остаются на loopback-порту.

## Порты и DNS

На роутере нужно направить на mini PC:

| Порт | Протокол | Назначение |
|---|---|---|
| `80` | TCP | ACME challenge и перенаправление на HTTPS |
| `443` | TCP | Mini App, API и WebSocket signaling |
| `8555` | TCP и UDP | прямой WebRTC media transport go2rtc |

Не нужно публиковать:

| Порт | Доступ |
|---|---|
| `18080/tcp` | FastAPI, только `127.0.0.1` хоста |
| `1984/tcp` | go2rtc HTTP/API, только `127.0.0.1` хоста и Docker network |
| `8554/tcp` | go2rtc RTSP, только Docker network |
| `5432`, `6379` | только Docker network |

Создайте DNS `A`-запись поддомена на статический публичный IPv4. Самый простой
вариант — обычная DNS-запись без CDN proxy: WebRTC на `8555/tcp+udp` все равно
должен идти напрямую на mini PC. Если `80` или `443` уже занят другим reverse
proxy, нельзя запускать второй listener на том же адресе: перенесите маршруты из
`docker/miniapp.Caddyfile` в существующий gateway.

## Runtime-настройка

Не копируйте реальные значения в репозиторий. В `.env` на сервере укажите имя
хоста без схемы и пути:

```dotenv
STH_PUBLIC_HOST=miniapp.example.com
COMPOSE_PROFILES=miniapp
```

Профиль нужен намеренно: пока `COMPOSE_PROFILES` не содержит `miniapp`,
gateway не запускается и не занимает `80/443`. Это позволяет сначала проверить
занятые порты и существующий reverse proxy, а затем явно включить публикацию.

В `config/config.yaml` URL должен соответствовать этому хосту:

```yaml
telegram:
  # Полный доступ администратора; существующие опасные Telegram-команды
  # по-прежнему проверяют этот же список.
  admin_user_ids:
    - 111111111

webapp:
  enabled: true
  public_url: "https://miniapp.example.com"
  # ID основной supergroup начинается с -100.
  primary_chat_id: -1001234567890
  # Наблюдатели получают только чтение Mini App.
  viewer_user_ids:
    - 222222222
  require_group_membership: true
  membership_recheck_sec: 300
```

Администраторы получают роль `admin`. Остальные пользователи должны быть
одновременно перечислены в `viewer_user_ids` и состоять в `primary_chat_id`;
пустой allowlist никому случайно доступ не открывает. Чтобы `getChatMember`
надежно проверял других участников, добавьте бота администратором основной
группы. Наблюдателям доступны только трансляции, архив, скачивание и климат;
управляющие действия остаются за администраторами. Членство повторно
проверяется с интервалом `membership_recheck_sec`, включая обращения по уже
выданным media/file tickets. Постоянное media-соединение ограничено десятью
минутами, а frontend переподключается заранее: это не дает уже открытому
WebSocket обходить последующие проверки доступа до конца часового ticket.

В runtime `config/go2rtc.yaml` оставьте listener и замените TEST-NET адрес из
example-файла на статический публичный IPv4:

```yaml
webrtc:
  listen: ":8555"
  candidates:
    - 203.0.113.10:8555  # пример; заменить на реальный адрес только на сервере
```

Проверьте, что `entrance`, `living` и `bed` объявлены одновременно в
`config/config.yaml` и `config/go2rtc.yaml`, а `go2rtc_stream` каждой камеры
совпадает с именем stream.

Если на mini PC используется отдельный runtime `compose.yaml`, перенесите в него
сервис `miniapp-gateway`, volumes Caddy и безопасные port bindings из
`docker-compose.yml`. Docker Compose предпочтет `compose.yaml`, поэтому одного
изменения репозиторного `docker-compose.yml` в этом случае недостаточно.

После ручной правки bind-mounted `config/config.yaml` или
`config/go2rtc.yaml` пересоздайте использующие их контейнеры явно:

```bash
docker compose up -d --force-recreate api go2rtc buffer miniapp-gateway
```

Для открытия приложения из домашней сети роутер должен поддерживать NAT
loopback. Если его нет, настройте split DNS: публичное имя Mini App внутри LAN
должно разрешаться в локальный адрес mini PC. Для WebRTC candidate при этом
может потребоваться оставить одновременно публичный и локальный адреса.

## Запуск и проверка

Перед первым запуском:

```bash
docker compose config --quiet
docker compose build miniapp-gateway
docker compose up -d
docker compose ps
```

Caddy получает публичный TLS-сертификат автоматически. DNS уже должен указывать
на сервер, а входящие `80/tcp` и `443/tcp` должны быть доступны. Проверки:

```bash
curl -fsS http://127.0.0.1:18080/health
curl -I https://miniapp.example.com/
docker compose logs --tail=100 miniapp-gateway go2rtc api
```

Затем зарегистрируйте точный HTTPS URL Mini App в BotFather. Приложение в
группе открывается обычной ссылкой бота с параметром `startapp`; кнопки
`web_app` в inline-клавиатуре предназначены для личного чата. После перезапуска
бот добавит ссылку в существующие панели камер и климата, а команда `/app`
создаст отдельную общую панель в текущем топике.

## Как защищена трансляция

Frontend сначала получает короткоживущий opaque ticket от FastAPI. Ticket
находится в пути, например:

```text
/media/t/<ticket>/api/ws?src=entrance
```

Перед проксированием Caddy вызывает
`/api/webapp/v1/media/authorize`. Только после успешной проверки он удаляет
ticket-префикс и отправляет разрешенный маршрут в go2rtc. Это также сохраняет
ticket во вложенных HLS URL, где query-параметр исходного WebSocket был бы
потерян.

Allowlist содержит только:

- `/api/ws`;
- `/api/stream.m3u8`;
- `/api/hls/playlist.m3u8`;
- `/api/hls/segment.ts`;
- `/api/hls/init.mp4`;
- `/api/hls/segment.m4s`.

Web UI, управление streams, конфигурация и прочие endpoints go2rtc через
публичный gateway недоступны.

## Нужен ли proxy

`TELEGRAM_PROXY_URL` относится только к исходящим запросам бота к Telegram API.
Если mini PC напрямую соединяется с Telegram, оставьте его пустым. Этот proxy не
публикует Mini App и не заменяет домен, HTTPS или port forwarding.

Для Mini App нужен доступ пользователей к вашему HTTPS-домену. Если конкретная
сеть не пропускает Telegram API, proxy можно включить только для бота. Если сеть
не дает прямой WebRTC даже при открытом `8555`, следующий вариант расширения —
TURN-сервер; обычный HTTP/SOCKS proxy эту задачу не решает.

CDN proxy для DNS-записи не обязателен. При статическом IP проще оставить
обычную `A`-запись: HTTPS идет прямо в Caddy, а `8555/tcp+udp` — прямо в
go2rtc. Даже при использовании CDN медиапорт все равно нельзя провести через
обычный HTTP proxy.

## Диагностика

- Сертификат не выпускается: проверить DNS, NAT для `80/443` и отсутствие
  другого процесса на этих портах.
- SPA открывается, но API отвечает `401/403`: проверить Telegram init data,
  allowlist пользователей и членство в основной группе.
- WebSocket получает `401/404`: проверить время жизни stream ticket и совпадение
  `go2rtc_stream`.
- WebRTC не соединяется извне: проверить оба протокола `8555`, публичный
  candidate и firewall; MSE/HLS останутся HTTPS fallback.
- Не открывать `1984` «для проверки» в интернет. Используйте
  `http://127.0.0.1:1984` только локально или `docker compose exec`.
