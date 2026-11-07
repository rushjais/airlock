# One image, one URL: the API serves the built frontend; the payment executor runs as a second process in the
# same container, bound to localhost only. Boots with DEMO_MODE=1 and no secrets (only DATABASE_URL).

FROM node:20.19-alpine AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.17 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/airlock ./airlock
COPY fixtures /app/fixtures
COPY scenarios /app/scenarios
COPY invoices /app/invoices
COPY demo /app/demo
COPY --from=web /app/frontend/dist /app/frontend/dist
COPY scripts/start.sh /app/start.sh
RUN useradd --create-home airlock && chmod +x /app/start.sh
USER airlock
ENV DEMO_MODE=1 AIRLOCK_NO_DOTENV=1 EXECUTOR_URL=http://127.0.0.1:8001 PORT=8000
EXPOSE 8000
CMD ["/app/start.sh"]
