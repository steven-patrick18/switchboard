# =============================================================================
# Switchboard Web — production image
#
# Three-stage build:
#   deps    — install node_modules (cacheable)
#   build   — compile Next.js to a standalone server bundle
#   runtime — slim runtime image carrying only the standalone output
#
# NEXT_PUBLIC_API_URL is baked at build time (Next inlines NEXT_PUBLIC_* vars
# into the client bundle), so set it as a build arg on Railway. Switching API
# URLs after deploy requires a rebuild — that's a Next.js constraint, not us.
# =============================================================================

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi


FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_API_URL must be available at build time, not runtime.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

RUN npm run build


FROM node:20-alpine AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Pull only the standalone output + static assets + public dir.
COPY --from=build /app/public ./public
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static

EXPOSE 3000

CMD ["node", "server.js"]
