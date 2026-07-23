# Security Policy

## Supported Versions

Only the latest released version receives security fixes.

## Reporting a Vulnerability

**Please do not open a public issue for security vulnerabilities.**

Use GitHub's private vulnerability reporting:
**Security** tab → **Report a vulnerability**

We aim to acknowledge reports within 7 days.

## Scope

Relay-agent executes provider CLIs in permission-bypass modes by design.
The following are **in scope**:

- Path traversal escaping configured input/output/artifact roots
- Local daemon authentication bypass (token handling, RPC surface)
- Command injection through task files, attachments, or CLI arguments
- Privilege escalation beyond the invoking OS account
- Artifact delivery writing outside declared destinations

The following are **out of scope** (documented design limits, see
[`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md)):

- Malicious code already running under the same OS account as Relay
- Factual accuracy or reasoning quality of AI-generated content
- Provider CLI vulnerabilities (report those to the respective vendor)

## Operational Security

For deployment hardening — dedicated accounts, ACLs, isolation
requirements — see [`docs/SECURITY.md`](docs/SECURITY.md).
