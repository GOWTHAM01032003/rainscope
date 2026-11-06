# FastAPI image. ecCodes is installed explicitly for GRIB metadata/value access.
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    RAINSCOPE_CACHE_DIR=/data/cache
RUN apt-get update \
    && apt-get install -y --no-install-recommends libeccodes0 libeccodes-data libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
RUN mkdir -p /data/cache \
    && useradd --system --uid 10001 --create-home rainscope \
    && chown -R rainscope:rainscope /data /app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import json,os,sys,urllib.request; port=os.getenv('PORT','8000'); host=os.getenv('RENDER_EXTERNAL_HOSTNAME','127.0.0.1'); request=urllib.request.Request(f'http://127.0.0.1:{port}/api/ready', headers={'Host':host}); response=json.load(urllib.request.urlopen(request, timeout=3)); sys.exit(0 if response.get('status')=='ready' and response.get('cache_available') is True else 1)" || exit 1
CMD ["sh", "-c", "exec python -m uvicorn backend.app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1"]
