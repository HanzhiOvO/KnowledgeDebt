# Contributing

Thank you for helping improve KnowledgeDebt.

1. Open an issue for substantial product or schema changes.
2. Create a focused branch and keep commits reviewable.
3. Run `make verify` before opening a pull request.
4. Never commit API keys, course recordings, private notes, databases, or copyrighted course material.
5. Describe which platforms you actually tested.

Changes must preserve these invariants:

- a Course Session exists independently of a recording;
- external sources cannot be presented as evidence of what a teacher said;
- viewing content never clears debt; a target-aware mastery assessment does;
- reconstruction confidence and learning coverage are separate metrics;
- AI-generated questions stay within supplied course evidence and expected mastery.

