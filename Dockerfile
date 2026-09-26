# tgmirror runs once per `docker run` and exits — no daemon, no cron inside the image (see
# docs/06-lo-trinh.md, Phase 12). `ENTRYPOINT ["tgmirror"]` so every command is
# `docker run --rm ... tgmirror <command>`, and `CMD ["--help"]` so a bare `docker run` is safe.

FROM python:3.11-slim AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first (cached across builds that only change src/): cryptg is a C extension, so a
# platform without a prebuilt wheel needs a compiler here. Not shipped in the final image.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.11-slim

RUN groupadd --gid 1000 tgmirror \
    && useradd --uid 1000 --gid tgmirror --create-home --shell /usr/sbin/nologin tgmirror

COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    XDG_CONFIG_HOME=/config \
    XDG_DATA_HOME=/data \
    PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring \
    TZ=UTC

# /data and /config are named volumes by default; a bind-mounted host directory needs its own
# ownership set to 1000:1000 (or `docker run --user "$(id -u):$(id -g)"`) since bind mounts keep
# the host's permissions instead of the ones baked in here.
RUN mkdir -p /data /config && chown -R tgmirror:tgmirror /data /config
VOLUME ["/data", "/config"]

USER tgmirror
WORKDIR /home/tgmirror
ENTRYPOINT ["tgmirror"]
CMD ["--help"]
