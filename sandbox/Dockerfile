FROM python:3.11-slim

# Только стандартная библиотека + базовые пакеты для вычислений
RUN pip install --no-cache-dir \
    numpy==1.26.4 \
    pandas==2.2.1

# Рабочая директория
WORKDIR /sandbox

# Непривилегированный пользователь
RUN useradd -m -u 1000 sandbox
USER sandbox

# Точка входа — читаем код из stdin
CMD ["python", "-"]