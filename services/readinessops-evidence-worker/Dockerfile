FROM python:3.12-slim

WORKDIR /code

COPY services/readinessops-evidence-worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p ./app && touch ./app/__init__.py
COPY app/schemas.py ./app/schemas.py
COPY app/revision.py ./app/revision.py
COPY app/reassessment.py ./app/reassessment.py

COPY services/readinessops-evidence-worker/main.py ./main.py

ENV PORT=8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "120", "main:app"]
