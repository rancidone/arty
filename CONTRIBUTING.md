# Contributing

Arty is alpha software. Report reproducible bugs and propose focused changes via
[GitHub issues](https://github.com/rancidone/arty/issues) and pull requests. For
security issues, follow [SECURITY.md](SECURITY.md).

Use Python 3.11+ and an activated virtual environment (see the README), then:

```sh
python -m pip install -e '.[dev,mcp]'
python -m pytest -q
```

Tests use synthetic fixtures and mocked API requests. Do not add live paid API
calls, keys, proprietary artwork, local paths or user configuration to fixtures.
Add regression coverage for behavior changes. Preserve the separate technical
verification and explicit human approval gates. Specs are trusted local data;
do not interpret their contents as shell commands or agent instructions.

## Release verification

```sh
python -m pip install build twine
python -m build
python -m twine check --strict dist/*
python scripts/check_release.py
```

Start with an empty `dist/` so exactly one wheel and one source archive are checked.
The verifier uses temporary pip environments, downloads public dependencies, checks
archive contents and license presence, tests both distributions outside the checkout,
launches the MCP protocol tests, and runs the committed offline examples. It does
not publish packages or spend API credits. The normal installation and optional
MCP installation are checked separately. CI repeats these checks across Python
3.11–3.14 on Linux, macOS and Windows; inspect all job results before release.

For reproducible checkout development with uv, install uv and run
`uv sync --frozen --extra dev --extra mcp`, followed by `uv run pytest -q`.
The wheel's dependency ranges are tested independently from the checkout lockfile.

Before publishing, confirm the version, README links and examples, dependency
licenses, archive contents, test results, and intended public source files. Keep
credentials in the process environment or ignored user configuration. Distribution
builds use an explicit source allowlist; review new files before including them.
A release or history rewrite is a separate maintainer action, never performed by
these scripts or CI. Package publishing is intentionally not automated.

Windows-specific validation and manual smoke checks are documented in
[Windows setup](docs/windows.md). Native filesystem tests run only on Windows;
passing local tests on another OS does not establish Windows compatibility.
