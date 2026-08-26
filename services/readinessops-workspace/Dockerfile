FROM python:3.12-slim

WORKDIR /srv

COPY services/readinessops-workspace/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /srv/templates

# Reuse the existing governed backend implementation.
# The Workspace is only an operating layer; it does not duplicate governance logic.
COPY app /srv/app

# Governance Workspace reuses the governed backend modules,
# but must not auto-import the ADK Agent Runtime from app/__init__.py.
# Runtime A remains the separate AI analysis runtime.
RUN printf '' > /srv/app/__init__.py

COPY services/readinessops-workspace/main.py /srv/main.py
COPY services/readinessops-workspace/templates/index.html /srv/templates/index.html

# Fail the image build if the Workspace cannot import the governed backend.
RUN python -c "import main; print('WORKSPACE CONTAINER IMPORT: PASS')"

ENV PORT=8080
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN groupadd --system appgroup \
    && useradd --system --uid 10001 --gid appgroup --no-create-home \
       --home-dir /nonexistent --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appgroup /srv

USER 10001:10001
RUN id

COPY services/readinessops-workspace/static/solifan-workspace-mark.png /app/static/solifan-workspace-mark.png
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 8 --timeout 180 main:app"]
