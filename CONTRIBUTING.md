# Contributing

Thank you for improving the connector.

1. Open an issue before substantial behavioral or schema changes.
2. Fork the repository and create a focused branch.
3. Never use a real `Notes.db` in tests; build an isolated fixture.
4. Preserve the write invariants: graceful close, verified backup, one transaction, integrity check, and conditional reopen.
5. Run `python -m unittest discover -s tests -v` on Windows.
6. Submit a pull request describing the behavior change, tests, and compatibility impact.

Commits should be concise and scoped. Do not commit generated databases, backups, virtual environments, vendored dependencies, personal paths, or credentials.
