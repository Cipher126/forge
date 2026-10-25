FROM node:24-alpine AS build
WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

FROM nginx:1.31.6-alpine3.24-slim
COPY --from=build /dashboard/dist /usr/share/nginx/html
COPY dashboard/nginx.conf /etc/nginx/forge.conf.template
ENV FORGE_API_UPSTREAM=api:8000
USER 101:101
EXPOSE 8080
ENTRYPOINT ["/bin/sh", "-c", "envsubst '${FORGE_API_UPSTREAM}' < /etc/nginx/forge.conf.template > /tmp/nginx.conf && exec nginx -c /tmp/nginx.conf -g 'daemon off;'"]
