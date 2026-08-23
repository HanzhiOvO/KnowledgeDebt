FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY backend/requirements.txt backend/requirements-local-asr.txt backend/requirements-postgres.txt /app/backend/
ARG WITH_LOCAL_ASR=1
RUN pip install --upgrade pip     && pip install -r /app/backend/requirements.txt     && pip install -r /app/backend/requirements-postgres.txt     && if [ "$WITH_LOCAL_ASR" = "1" ]; then pip install -r /app/backend/requirements-local-asr.txt; fi

COPY backend /app/backend
WORKDIR /app/backend
EXPOSE 8123

CMD ["sh", "-c", "alembic -c alembic.ini upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8123"]
