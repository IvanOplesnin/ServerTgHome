FROM node:24-alpine AS frontend

WORKDIR /build

COPY webapp/package.json webapp/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY webapp/ ./
RUN npm run build


FROM nginxinc/nginx-unprivileged:1.28.1-alpine

COPY docker/miniapp.nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend /build/dist /usr/share/nginx/html

RUN nginx -t

EXPOSE 8080
