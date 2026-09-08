# syntax=docker/dockerfile:1
#
# Worker image for the Castrol video pipeline.
#
# Runs any `castrol <cmd>` — drain, work, poll, intake, schedule. One image,
# many entrypoints; which command a container runs is a deploy-time choice.
#
# This image contains NO credentials. Everything in .env.example is supplied at
# run time (`--env-file`, or the EC2 host's environment). .dockerignore excludes
# .env so a stray local file cannot be baked in.

# ---------------------------------------------------------------- builder --
FROM python:3.12-slim-bookworm AS builder

# Pinned, not :latest — a uv upgrade must be a visible commit, not a silent
# change in how the lockfile resolves.
COPY --from=ghcr.io/astral-sh/uv:0.9.14 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies as their own layer, before any source is copied. src/ changes on
# every commit and the lockfile almost never does, so a code-only push reuses
# this layer instead of re-resolving the whole tree.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-dev --no-install-project

COPY pyproject.toml uv.lock ./
COPY src/ src/

# --no-editable puts a real copy of the package inside .venv. The default
# editable install writes a .pth pointing at /app/src, which resolves to
# nothing once only .venv is carried into the runtime stage.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------- runtime --
FROM python:3.12-slim-bookworm AS runtime

# ffmpeg carries ffprobe, and both are load-bearing: probe_duration_seconds()
# sits in the charge path for the video stage, which bills per output second
# (invariant 12). A missing ffprobe is not a crash, it is a wrong bill.
#
# fonts-dejavu-core is what render_card() actually finds here. Its font list is
# ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf") and only the last
# exists on Debian; without this package Pillow falls through to
# ImageFont.load_default(), a tiny bitmap face, and every card ships wrong.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-dejavu-core \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root. The pipeline's IAM user cannot delete S3 objects by design; the
# container process should not be able to scribble over its own install either.
RUN useradd --create-home --uid 10001 castrol

WORKDIR /app
COPY --from=builder --chown=castrol:castrol /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Belt and braces against a run-time surprise: stub stages are the default only
# when someone says so, and delivery — the one irreversible outward action
# (invariant 28) — stays off unless the deploy turns it on explicitly.
ENV USE_STUB_STAGES=false \
    DELIVERY_ENABLED=false

USER castrol

ENTRYPOINT ["castrol"]
CMD ["--help"]
