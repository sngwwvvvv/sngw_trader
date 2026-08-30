FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md /app/
COPY src /app/src
RUN pip install --no-cache-dir -e .

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "sngw_trader.runners.live_okx"]
