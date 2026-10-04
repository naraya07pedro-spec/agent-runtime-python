FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
WORKDIR /srv
RUN pip install --no-cache-dir uv==0.12.19 && useradd --create-home --uid 10001 runtime
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --locked --no-dev
ENV PATH="/srv/.venv/bin:$PATH"
USER 10001
EXPOSE 8000
CMD ["uvicorn","app.api:create_app","--factory","--host","0.0.0.0","--port","8000","--no-access-log"]
