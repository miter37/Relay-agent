# Contributing to Relay-agent

First off, thank you for considering contributing to Relay-agent! We welcome PRs, bug reports, and suggestions.

## Development Setup

1. **Clone the repository:**
   ```sh
   git clone https://github.com/miter37/Relay-agent.git
   cd Relay-agent
   ```
2. **Ensure Python 3.11+ is installed.**
3. **Run local tests:**
   Tests use bundled mock CLIs and never call real provider APIs, so no
   credentials or network access are required.
   ```sh
   python -m unittest discover -s tests -v
   ```
4. **Build the release artifact (optional):**
   ```sh
   python build_release.py
   ```
   This produces `relay.pyz` and `SHA256SUMS.txt` locally. Both are
   gitignored — they are published only as GitHub Release assets.

## Pull Request Process

1. Create a new branch from `master` (`git checkout -b feature/your-feature-name`).
2. Implement your changes.
3. Add or update tests as necessary.
4. Ensure all tests pass.
5. Submit a PR and fill out the provided template.

## Code Style

Formatting and linting are enforced by [ruff](https://docs.astral.sh/ruff/).
Before submitting a PR:

```sh
pip install ruff
ruff format .
ruff check . --fix
```

CI rejects PRs that fail `ruff format --check` or `ruff check`.

Ruff is a development tool only — Relay-agent itself has zero runtime
dependencies, and we intend to keep it that way.

Beyond what the tools enforce:

- Keep code simple and explicit.
- Avoid introducing heavy third-party dependencies; Relay-agent aims to use the Python standard library as much as possible.
