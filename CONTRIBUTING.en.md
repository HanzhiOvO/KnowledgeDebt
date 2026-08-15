# Contributing

Thank you for helping improve KnowledgeDebt. Chinese is the default language for project discussions, Issues, and Pull Requests, but English contributions are welcome.

1. Open an Issue before substantial product, provider-contract, privacy, or schema changes.
2. Create a focused branch from the latest `main` and keep commits reviewable.
3. Install dependencies with `make backend-install` and `make web-install`.
4. Run `make verify` and relevant Alembic migration tests before opening a Pull Request.
5. Never commit API keys, access tokens, recordings, private notes, databases, or unauthorized copyrighted material.
6. State which browsers, databases, and deployment paths were actually tested.

Preserve the core invariants: a Session is not a recording; external sources cannot prove classroom coverage; reading never clears debt; reconstruction and learning coverage stay separate; assessments remain within supplied evidence and expected mastery.

The primary product is `web/` plus `backend/`. Database changes require ORM metadata, an Alembic revision, SQLite compatibility, and upgrade tests. External-provider changes require an updated consent manifest and proof that upload cannot transmit data without consent.
