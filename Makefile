.PHONY: help start dev backend-install backend-run backend-test backend-lint migrate web-install web-run web-test legacy-client-get legacy-client-run legacy-client-test compose-up compose-down verify smoke-local-asr

MEDIA ?=
SECONDS ?= 60

help:
	@echo "KnowledgeDebt 常用命令"
	@echo "  make start           自动补齐依赖并同时启动 Web 与 API"
	@echo "  make dev             使用现有依赖同时启动 FastAPI 与 Next.js"
	@echo "  make verify          运行后端检查、测试与 Web 生产构建"
	@echo "  make backend-test    运行 Pytest"
	@echo "  make web-test        运行 ESLint、TypeScript 与 Next.js 构建"
	@echo "  make migrate         执行 Alembic 数据库迁移"
	@echo "  make compose-up      构建并启动 Docker Compose 服务"
	@echo "  make smoke-local-asr MEDIA=录音.aac [SECONDS=60]  本地 ASR 真机速度/质量测试"

start:
	./start.sh

dev:
	@$(MAKE) -j2 backend-run web-run

backend-install:
	python3 -m venv .venv
	.venv/bin/pip install -r backend/requirements-dev.txt

backend-run:
	cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8123

backend-test:
	cd backend && ../.venv/bin/python -m pytest -q

backend-lint:
	cd backend && ../.venv/bin/ruff check .

migrate:
	cd backend && ../.venv/bin/alembic -c alembic.ini upgrade head

web-install:
	cd web && npm ci

web-run:
	cd web && npm run dev

web-test:
	cd web && npm run lint && npm run build

legacy-client-get:
	cd legacy/flutter-client && flutter pub get

legacy-client-run:
	cd legacy/flutter-client && flutter run

legacy-client-test:
	cd legacy/flutter-client && flutter analyze && flutter test

compose-up:
	docker compose up --build

compose-down:
	docker compose down

smoke-local-asr:
	@test -n "$(MEDIA)" || { echo "用法：make smoke-local-asr MEDIA=录音.aac [SECONDS=60]"; exit 1; }
	.venv/bin/python backend/scripts/local_asr_smoke.py "$(MEDIA)" --seconds $(SECONDS)

verify: backend-lint backend-test web-test
