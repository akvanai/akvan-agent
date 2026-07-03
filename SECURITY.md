# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Akvan Agent, please **do not** open a
public issue. Instead, report it privately:

- **Email**: d1arashd1arash@gmail.com
- **Subject**: "Security: [brief description]"

You should receive a response within 48 hours. We take all reports seriously and
will work with you to understand and address the issue.

## Scope

Security concerns include but are not limited to:

- Unauthorized file access or writes outside the project root.
- Command injection through tool arguments.
- Credential leaks (API keys, tokens) via logs, errors, or stored sessions.
- Bypass of the approval system for dangerous operations.
- Gateway transport vulnerabilities (Telegram bot token exposure, etc.).

## Safe Defaults

Akvan Agent applies several safeguards by default:

- Dangerous terminal commands and sensitive file writes require approval.
- Approval is denied when no choice is made before the timeout (60s default).
- Catastrophic host commands are always blocked, even in `--yolo` mode.
- `.env` and credential files are excluded from git via `.gitignore`.
- Session databases use restricted file permissions.

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | :white_check_mark: |

## Disclosure

After a fix is released, we will publish a security advisory and credit the
reporter (unless anonymity is requested).
