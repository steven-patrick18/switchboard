# =============================================================================
# Switchboard API — production image
#
# Multi-stage build. The first stage installs deps into a virtualenv; the
# second stage carries only the venv + app code into a slim runtime layer so
# the deployed image is small (~150 MB vs. 600 MB for a single-stage build).
#
# On boot, the entrypoint applies any pending Alembic migrations BEFORE
# starting uvicorn. So a fresh Railway deploy "just works" — no manual
# migration step the operator has to remember.
# =============================================================================

FROM python:3.12-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Compiler toolchain only needed to build wheels for cryptography / bcrypt.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN python -m venv /venv \
 && /venv/bin/pip install --upgrade pip \
 && /venv/bin/pip install -e .

# Bring in the rest of the source AFTER deps so deps stay layer-cached.
COPY . .


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/venv/bin:$PATH"

WORKDIR /app

# Minimal runtime libs (libssl/libpq) — no compilers in the final image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /venv /venv
COPY --from=build /app /app

# Default port — Railway / most PaaS set $PORT at runtime; we fall back to 8000.
ENV PORT=8000
EXPOSE 8000

# Apply migrations on every boot. Idempotent: alembic skips already-applied
# revisions. If you don't want this on a given environment, override the
# command with `uvicorn ...` directly.
CMD ["sh", "-c", "python -m alembic upgrade head && python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
