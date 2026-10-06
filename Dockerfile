# pdfshrink — сжатие PDF до 10 МБ и меньше.
# Сборка:  docker build -t pdfshrink .
# Запуск:  docker run --rm -v "$PWD:/data" pdfshrink file.pdf
FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="pdfshrink" \
      org.opencontainers.image.description="CLI для сжатия PDF до заданного размера (по умолчанию 10 МБ)" \
      org.opencontainers.image.source="https://github.com/bybaleh/pdf_compressor" \
      org.opencontainers.image.licenses="MIT"

# ghostscript — основное сжатие, qpdf — попытка сжать без потери качества.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ghostscript \
        qpdf \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/pdfshrink \
    HOME=/tmp \
    MPLCONFIGDIR=/tmp

COPY pdfshrink/ /opt/pdfshrink/pdfshrink/

# /data — каталог, который монтируется с хоста (туда кладём PDF).
WORKDIR /data

# Контейнер отрабатывает одну команду и завершается (--rm убирает его следы).
ENTRYPOINT ["python", "-m", "pdfshrink"]
CMD ["--help"]
