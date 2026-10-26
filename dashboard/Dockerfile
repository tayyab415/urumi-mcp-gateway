# Build the static dashboard, then serve it with nginx. The gateway and the chat
# service are separate Deployments; Ingress routes /api and /mcp to them.
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# The unprivileged image runs as uid 101 and listens on 8080, so the Pod needs no root.
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
