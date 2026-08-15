.PHONY: dev backend-install backend-run backend-test backend-lint web-install web-run web-test legacy-client-get legacy-client-run legacy-client-test verify

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

verify: backend-lint backend-test web-test
