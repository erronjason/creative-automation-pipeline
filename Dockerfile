# UNTESTED: written but never built or run. Kept on the docker-untested branch, not on main.
FROM python:3.12-slim
# libraqm enables complex-script shaping (Arabic, Hebrew, Devanagari) in Pillow text rendering.
RUN apt-get update && apt-get install -y --no-install-recommends libraqm0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[s3]"
COPY brand ./brand
COPY legal ./legal
COPY briefs ./briefs
COPY assets ./assets
EXPOSE 8765
CMD ["cap", "serve", "--host", "0.0.0.0", "--no-open"]
