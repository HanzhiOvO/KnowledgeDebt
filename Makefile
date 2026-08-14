.PHONY: backend-install backend-run backend-test backend-lint client-get client-run client-test verify

backend-install:
	python3 -m venv .venv
	.venv/bin/pip install -r backend/requirements-dev.txt

backend-run:
	cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8123

backend-test:
	cd backend && ../.venv/bin/pytest -q

backend-lint:
	cd backend && ../.venv/bin/ruff check .

client-get:
	cd client && flutter pub get

client-run:
	cd client && flutter run

client-test:
	cd client && flutter analyze && flutter test

verify: backend-lint backend-test client-test

