FROM python:3.12-slim

WORKDIR /app

COPY services/readinessops-judge-ui/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/readinessops-judge-ui/main.py .
RUN mkdir -p /app/static
COPY docs/architecture/readinessops-architecture.html /app/static/readinessops-architecture.html

ENV PORT=8080

COPY services/readinessops-judge-ui/static/solifan-judge-mark.png /app/static/solifan-judge-mark.png
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 8 --timeout 120 main:app"]
