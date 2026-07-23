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

CI runs the full suite on Windows, macOS, and Linux across Python 3.11–3.13
for every PR. All nine combinations must pass before a PR is merged.

## Commit Messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>: <short summary in imperative mood>
```

| Type       | Use for                                                 |
|------------|---------------------------------------------------------|
| `feat`     | A new user-facing capability                             |
| `fix`      | A bug fix                                                |
| `docs`     | Documentation only                                       |
| `spec`     | Design documents under `docs/superpowers/specs/`         |
| `test`     | Adding or fixing tests                                   |
| `refactor` | Code change with no behavior change                      |
| `style`    | Formatting only (no logic change)                        |
| `chore`    | Build, CI, release, tooling                              |
| `security` | Security-relevant change (also open a `security` issue)  |

Examples:

```
feat: add relay update command with checksum verification
fix: terminate orphaned browser helpers on Windows job object teardown
docs: correct clone URL in CONTRIBUTING
chore: release v0.6.0
```

Keep the summary under 72 characters. Put details in the body if needed.

## Documentation Language

This project maintains documentation in two languages by design:

| Audience                  | Language | Files |
|---------------------------|----------|-------|
| Public / contributors     | English  | `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `docs/superpowers/**`, all CLI `--help` output |
| Operators / agent authors | Korean   | `manual.md`, `skills/hermes-relay/SKILL.md`, `docs/KNOWN_LIMITATIONS.md` |

When editing a file, **match the language already used in that file.** Do not
translate existing documents as part of an unrelated PR — open a separate
issue first if you believe a document should switch languages.

## Releasing

Releases are built by `.github/workflows/release.yml` and triggered by pushing
a tag. Nothing is built or uploaded by hand.

1. Bump the version in **`relay/__init__.py`** — this is the single source of
   truth. `pyproject.toml` reads it.
2. Rewrite `RELEASE_NOTES.md` for the new version. The first line must contain
   the version number; the whole file becomes the release body.
3. Commit, tag, and push:

   ```sh
   git add relay/__init__.py RELEASE_NOTES.md
   git commit -m "chore: release v0.6.0"
   git tag v0.6.0
   git push origin master --tags
   ```

The workflow then verifies the tag matches `relay.__version__`, verifies
`RELEASE_NOTES.md` is for that version, runs the test suite, builds
`relay.pyz` with matching `SHA256SUMS.txt`, executes the built artifact, and
publishes the release with both files attached.

Any of those checks failing stops the run **before** the release is created,
so a mislabelled or broken release cannot reach users.

`relay.pyz` and `SHA256SUMS.txt` are never committed — GitHub Release assets
are the only place they are published, and the only place `relay update`
should fetch them from.

## Reporting Security Issues

Do not open a public issue for a suspected vulnerability. See
[`SECURITY.md`](SECURITY.md) for the private reporting process.

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
