FROM node:24-alpine AS build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ARG VITE_API_BASE_URL=
ARG VITE_BASEMAP_STYLE_URL=
ARG VITE_BASEMAP_TILE_URL=
ARG VITE_BASEMAP_ATTRIBUTION=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_BASEMAP_STYLE_URL=${VITE_BASEMAP_STYLE_URL} \
    VITE_BASEMAP_TILE_URL=${VITE_BASEMAP_TILE_URL} \
    VITE_BASEMAP_ATTRIBUTION=${VITE_BASEMAP_ATTRIBUTION}
RUN npm run build

FROM nginx:stable-alpine
COPY --from=build /src/frontend/dist/ /usr/share/nginx/html/
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q --spider http://127.0.0.1:8080/ || exit 1
