# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --fetch-retries=5 --fetch-retry-mintimeout=20000 --fetch-retry-maxtimeout=120000

FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 NODE_ENV=production
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# YMVA's n8n node, served at /downloads/n8n-nodes-ymva.tgz so n8n can install it in one line.
FROM node:22-alpine AS n8n-node
WORKDIR /node
COPY --from=n8n-src package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --ignore-scripts
COPY --from=n8n-src tsconfig.json README.md ./
COPY --from=n8n-src nodes ./nodes
COPY --from=n8n-src credentials ./credentials
RUN npm run build && npm pack && mv n8n-nodes-ymva-*.tgz n8n-nodes-ymva.tgz

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
RUN addgroup -S -g 10001 ymva && adduser -S -u 10001 -G ymva ymva
COPY --from=build --chown=ymva:ymva /app/.next/standalone ./
COPY --from=build --chown=ymva:ymva /app/.next/static ./.next/static
COPY --from=build --chown=ymva:ymva /app/public ./public
COPY --from=n8n-node --chown=ymva:ymva /node/n8n-nodes-ymva.tgz ./public/downloads/
USER ymva
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -qO- http://127.0.0.1:3000/login >/dev/null || exit 1
CMD ["node", "server.js"]
