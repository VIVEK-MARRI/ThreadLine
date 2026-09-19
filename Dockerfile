# ThreadLine API — production image (Railway / Fly / any container host).
#
# Builds the frontend bundle into the image for hosts that serve it
# separately (object storage, CDN, or a static service proxying /api/*
# to this container). The API itself serves /api/* and /health only.
#
# Required runtime env (see DEPLOYMENT.md):
#   SOURCE_REPOSITORY_BACKEND=database
#   BACKGROUND_WORKER_ENABLED=true
#   AUTH_OPEN_BOOTSTRAP=true (first boot only, then false)
# Mount a persistent volume at /data and set
#   SOURCE_DATABASE_PATH=/data/threadline.db

# ---- Frontend build ----
FROM node:22-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- API runtime ----
FROM python:3.13-slim
WORKDIR /srv/threadline
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY --from=frontend /build/frontend/dist ./frontend/dist
ENV SOURCE_REPOSITORY_BACKEND=database \
    BACKGROUND_WORKER_ENABLED=true \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
