FROM python:3.11-slim

# MEDIA-1/3: ffmpeg for video frames, poppler-utils for PDF-to-image
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg poppler-utils && \
    rm -rf /var/lib/apt/lists/*

# Только стандартная библиотека + базовые пакеты для вычислений
RUN pip install --no-cache-dir \
    numpy==1.26.4 \
    pandas==2.2.1 \
    openpyxl \
    reportlab \
    pypdf2

# Рабочая директория
WORKDIR /sandbox

# Непривилегированный пользователь
RUN useradd -m -u 1000 sandbox && \
    mkdir -p /output && chown sandbox:sandbox /output && \
    chown sandbox:sandbox /sandbox
USER sandbox

# Точка входа — читаем код из stdin
CMD ["python", "-"]