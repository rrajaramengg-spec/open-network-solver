# open-routing-service-ui — multi-stage build (Node → nginx).

ARG NODE_VERSION=20
ARG NGINX_VERSION=1.27-alpine

# ---------- builder ----------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS builder

WORKDIR /build

# Env vars baked in at build time (Vite resolves VITE_* statically).
ARG VITE_ROUTING_API_URL=http://localhost:58000
ARG VITE_NOMINATIM_URL=http://localhost:58080
ARG VITE_TILE_URL=https://tile.openstreetmap.org/{z}/{x}/{y}.png
ENV VITE_ROUTING_API_URL=$VITE_ROUTING_API_URL \
    VITE_NOMINATIM_URL=$VITE_NOMINATIM_URL \
    VITE_TILE_URL=$VITE_TILE_URL

COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY tsconfig.json tsconfig.node.json vite.config.ts index.html ./
COPY src ./src

RUN npm run build

# ---------- runtime ---------------------------------------------------------
FROM nginx:${NGINX_VERSION} AS runtime

# Drop privileges — alpine ships a non-root `nginx` user, we just need to
# adjust the listen port to a non-privileged one (8080) and rewire the user.
RUN sed -i 's/listen\s\+80;/listen 8080;/' /etc/nginx/conf.d/default.conf \
    && sed -i 's/listen\s\+\[::\]:80;/listen [::]:8080;/' /etc/nginx/conf.d/default.conf \
    && sed -i '/^user /d' /etc/nginx/nginx.conf \
    && sed -i 's|/var/run/nginx.pid|/tmp/nginx.pid|; s|/run/nginx.pid|/tmp/nginx.pid|' /etc/nginx/nginx.conf \
    && touch /tmp/nginx.pid \
    && chown -R nginx:nginx /var/cache/nginx /var/log/nginx /etc/nginx /tmp/nginx.pid

COPY --from=builder --chown=nginx:nginx /build/dist /usr/share/nginx/html

USER nginx
EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=5 \
    CMD wget -q --spider http://localhost:8080/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
