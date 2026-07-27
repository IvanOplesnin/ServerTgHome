FROM node:24-alpine AS frontend

WORKDIR /build

COPY webapp/package.json webapp/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY webapp/ ./
RUN npm run build


FROM caddy:2.11.4-alpine

COPY docker/miniapp.Caddyfile /etc/caddy/Caddyfile
RUN caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

COPY --from=frontend /build/dist /srv

EXPOSE 80 443
