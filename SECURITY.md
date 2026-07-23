# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Akvan Agent, please **do not** open a
public issue. Instead, report it privately:

- **Email**: agent@akvan.app
- **Subject**: "Security: [brief description]"

You should receive a response within 48 hours. We take all reports seriously and
will work with you to understand and address the issue.

## Scope

Security concerns include but are not limited to:

- Credential leaks (API keys, tokens) via logs, errors, stored sessions, or tools.
- Bypass of the approval system for dangerous operations.
- Command injection through tool arguments.
- Unauthorized writes to sensitive paths.
- Gateway transport vulnerabilities (Telegram bot token exposure, etc.).
- Persistent prompt injection through memory, skills, or session content.

## What Akvan Is Not

Akvan is a **local agent harness**, not a sandbox. The CLI and authorized gateway
sessions run tools as your OS user. Regex-based approval and `read_file` path
blocks are **defense-in-depth** — they help well-behaved models and surface
audit trails, but a determined model or malicious instruction can often bypass
them via the `terminal` tool or other host access.

## File Read Policy

- `read_file` can read absolute paths **outside** the current project root.
- `read_file` **blocks** known sensitive paths, including:
  - SSH and common credential directories under `$HOME` (e.g. `~/.ssh/`)
  - Secret-bearing env files (`.env`, `.env.local`, `.envrc`, etc.) anywhere on disk
  - Akvan credential stores under `~/.akvan/` (`.env`, `approvals.json`)
  - Selected system files (e.g. `/etc/shadow`, `/etc/sudoers`)
- Reads under `~/.akvan/skills/` are allowed (skills are meant to be loaded).
- The `terminal` tool is **not** subject to the same read denylist and can still
  read blocked paths unless caught by command approval rules.

## File Write Policy

- Ordinary project edits run without prompts.
- Writes under the agent media vault (`~/.akvan/vault` by default) are allowed
  without the sensitive-`~/.akvan` approval prompt. The vault is for media and
  files only — not credentials or config.
- Other dangerous or sensitive writes (outside project root, `.env`, `.ssh`,
  non-vault `~/.akvan/` paths, etc.) require explicit approval unless bypassed by
  `--yolo` or `AKVAN_APPROVAL_MODE=off`.

## Terminal Policy

- Dangerous commands are detected via regex patterns and require approval by default.
- Catastrophic host commands are always blocked, even in `--yolo` mode.
- Approval times out closed (60s default) when no choice is made.
- This is **not** full command containment — many exfiltration or read paths are not
  matched by the pattern list.

## Background Review

After every N user turns (default 10) and every N tool iterations (default 10),
Akvan may run a background review fork that writes to memory and skills **directly**
without interactive approval:

- Saved memory and skills are injected into future system prompts.
- Loaded skills are treated as **trusted instructions**.
- Background review **cannot modify bundled skills**; other skills may be created or
  patched autonomously.
- Untrusted content from tools, web pages, or messages could influence what gets saved.
  Set `memory.nudge_interval: 0` and `skills.creation_nudge_interval: 0` in
  `~/.akvan/config.yaml` to disable autonomous review.

## Gateway Policy

- Authorized gateway users receive the **same tool set** as the local CLI (files,
  terminal, memory, skills, etc.).
- Sensitive operations use inline approval buttons when configured for manual approval.
- `--yolo` and `AKVAN_APPROVAL_MODE=off` apply to gateway sessions when configured.
  Per-chat approval preferences stored in `state.db` override the global env default.
- Gateway access is restricted by platform allowlists (e.g. Telegram user IDs).

## Safe Defaults

Akvan applies several safeguards by default:

- Dangerous terminal commands and sensitive file writes require approval.
- Approval is denied when no choice is made before the timeout (60s default).
- Catastrophic host commands are always blocked, even in `--yolo` mode.
- `read_file` blocks known credential and secret paths.
- `.env` and credential files are excluded from git via `.gitignore`.
- Telegram gateway access is fail-closed without an allowlist.

### State on disk

- `~/.akvan/` is created with mode `0700` (owner-only directory access).
- Session database files (`state.db` and WAL sidecars), logs, configuration,
  memory files, and credential files are created with mode `0600`.
- Existing installs are tightened automatically when Akvan starts.
- `browser_upload` sends file bytes to the browser runtime over HTTP (temp files
  inside the runtime). Docker does not mount the vault or banners for media;
  auth profiles remain on their own mount. Do not put secrets in the vault.

This protects stored session and configuration data from other local OS users.
It does not prevent network attackers or unrestricted tool execution from
accessing host files.

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | :white_check_mark: |

## Disclosure

After a fix is released, we will publish a security advisory and credit the
reporter (unless anonymity is requested).
