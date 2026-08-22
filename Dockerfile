# DeadSat Resurrection — backend + AI-1 + AI-2 + crypto (CY-1) image.
#
# This is ONE image for all three, not three separate ones, because the
# code itself is one process: main.py's FastAPI app mounts the emulator,
# the AI-1 classifier, the AI-2 recovery agent AND crypto_routes together
# (see README "Run from the repository root" — CY1_BASE defaults to
# in-process, confirmed by GET /crypto/status returning "in_process": true
# in a normal run). Splitting them into separate containers would require
# rearchitecting main.py into real microservices, which is a much bigger
# change than "add Docker" — this containerizes what the code actually is.
#
# The RF ground station (rf/spectrum_display.py, Pi #2) and the frontend
# are intentionally NOT part of this image — the RF station expects a
# physical RTL-SDR device, and the frontend is a separate static/dev
# server (see docker-compose.yml comments).
FROM python:3.11-slim AS base

# build-essential/libffi/libsodium: PyNaCl and a couple of transitive deps
# ship manylinux wheels for common platforms, but not every arch (e.g. this
# keeps arm64 hosts, common for Raspberry Pi and Apple Silicon dev
# machines, from failing pip install by falling back to a source build).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        libsodium-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so `docker build` reuses this layer whenever only
# application code changes, not requirements.txt.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Application code -------------------------------------------------
# Everything except frontend/ (see .dockerignore). Includes model_artifacts/
# if it exists in the build context (it is .gitignore'd, so a build from a
# FRESH clone will NOT have it — see the note at the bottom of this file).
COPY . .

# main.py MUST run with the repo root as the working directory — it does
# sys.path.append() on emulator/, agents/, crypto/, models/ as siblings,
# not packages (see README "Run from the repository root"). WORKDIR /app
# with `COPY . .` into /app satisfies that.

# DEADSAT_API_HOST/PORT default to 0.0.0.0:8000 already (config.py), so the
# container listens on all interfaces without any extra flags.
EXPOSE 8000

# No HEALTHCHECK CMD using curl/wget — neither is installed here to keep
# the image slim. docker-compose.yml's healthcheck uses Python's own
# standard library instead (see the compose file).

# If model_artifacts/ was NOT present in the build context (fresh clone,
# nothing trained yet), main.py still starts — /pipeline/status reports
# artifacts_ready: false and /pipeline/classify returns 503, exactly as it
# does outside Docker (see README "Train AI-1 (required once)"). Train
# inside the running container with:
#   docker compose exec backend python generate_dataset.py --propagator sgp4 --verify
#   docker compose exec backend python train_classifier.py
# then restart the container so classifier_inference.py picks up the new
# artifacts on the next startup load().
CMD ["python", "main.py"]
