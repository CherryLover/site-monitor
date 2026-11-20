# ============================================================================
#  site-monitor 镜像：uv 安装依赖（只装运行时），数据目录挂在 /data
# ============================================================================
FROM python:3.13-slim
LABEL org.opencontainers.image.source="https://github.com/CherryLover/site-monitor" \
      org.opencontainers.image.description="Umami + Google Search Console site monitor"

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" SM_DB_PATH=/data/site-monitor.db

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app

RUN useradd --system --uid 10001 monitor && mkdir -p /data && chown monitor /data
USER monitor
VOLUME ["/data"]
EXPOSE 8700
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8700/healthz', timeout=4)"
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8700", "--proxy-headers", "--forwarded-allow-ips", "*"]
