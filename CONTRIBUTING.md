# Contributing to Relay-agent

First off, thank you for considering contributing to Relay-agent! We welcome PRs, bug reports, and suggestions.

## Development Setup

1. **Clone the repository:**
   ```sh
   git clone https://github.com/miter37/Relay.git
   cd Relay
   ```
2. **Ensure Python 3.11+ is installed.**
3. **Run local tests:**
   Relay-agent includes mock tests that do not call real APIs.
   ```sh
   PATH="$PWD/mocks:$PATH" PYTHONPATH=. python -m unittest tests.test_relay.RelayTests -v
   ```

## Pull Request Process

1. Create a new branch from `master` (`git checkout -b feature/your-feature-name`).
2. Implement your changes.
3. Add or update tests as necessary.
4. Ensure all tests pass.
5. Submit a PR and fill out the provided template.

## Code Style

- Keep code simple and explicit.
- Follow PEP 8 guidelines where possible.
- Avoid introducing heavy third-party dependencies; Relay-agent aims to use the Python standard library as much as possible.
