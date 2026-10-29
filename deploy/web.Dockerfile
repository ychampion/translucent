# Static web export served by Caddy, which also proxies /api to the API container (one origin, no CORS).
FROM oven/bun:1.2.23 AS build
WORKDIR /web
COPY web/package.json web/bun.lock ./
RUN bun install --frozen-lockfile
COPY web/ ./
# Empty base: the browser calls /api on the same origin.
ENV NEXT_PUBLIC_API_BASE="" NEXT_TELEMETRY_DISABLED=1
RUN bun run build

FROM caddy:2.10.2-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /web/out /srv
