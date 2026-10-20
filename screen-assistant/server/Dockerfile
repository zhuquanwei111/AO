FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY screen_assistant_server ./screen_assistant_server

ENV PYTHONUNBUFFERED=1 \
    REDIS_URL=redis://redis:6379/0

EXPOSE 8000

CMD ["uvicorn", "screen_assistant_server.app:app", "--host", "0.0.0.0", "--port", "8000"]
