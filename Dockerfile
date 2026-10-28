# Readback monitor: the web UI and API on port 8000.
#   docker build -t readback .
#   docker run -p 8000:8000 --env-file .env readback
# Without MODEL_API_KEY the scripted scenarios and the tagger still work; Voice Transcribe,
# Muse Spark, recordings and occurrence reports need the key.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg espeak-ng \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.11.23

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1

# Dependencies first (CPU PyTorch for the tagger), so code changes rebuild in seconds.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-default-groups --group train --no-install-project

COPY src ./src
COPY web ./web
COPY models ./models
COPY training ./training
RUN uv sync --locked --no-default-groups --group train \
    && useradd --create-home readback \
    && mkdir -p scenarios results/raw \
    && chown -R readback scenarios results

USER readback
ENV PATH="/app/.venv/bin:$PATH" READBACK_HOST=0.0.0.0 READBACK_PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["readback-ui"]
