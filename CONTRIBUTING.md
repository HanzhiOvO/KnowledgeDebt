# Contributing

Thank you for helping improve KnowledgeDebt.

1. Open an issue for substantial product, provider-contract, privacy, or schema changes.
2. Create a focused branch from the latest `main` and keep commits reviewable.
3. Install dependencies with `make backend-install` and `make web-install`.
4. Run `make verify` and the relevant Alembic migration tests before opening a pull request.
5. Never commit API keys, access tokens, course recordings, private notes, databases, or copyrighted course material.
6. Describe which browsers, databases, and deployment paths you actually tested.

Changes must preserve these invariants:

- a Course Session exists independently of a recording;
- external sources cannot be presented as evidence of what a teacher said;
- viewing content never clears debt; a target-aware mastery assessment does;
- reconstruction confidence and learning coverage are separate metrics;
- AI-generated questions stay within supplied course evidence and expected mastery.

The primary product is `web/` plus `backend/`. `legacy/flutter-client/` is preserved for experiments and compatibility; new product work should not depend on Flutter unless a proposal explicitly restores a supported native scope.

Schema changes must update `backend/app/orm.py`, add an Alembic revision, preserve existing SQLite data, and include an upgrade test. External-provider changes must update the consent manifest and prove that uploads cannot trigger unconsented transmission.
