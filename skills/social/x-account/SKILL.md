---
name: x-account
description: Use Akvan's optional x_account tools for X auth checks, profile fetches, and confirmed posting.
version: 1.0.0
author: Akvan Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  akvan:
    tags: [x, twitter, posting, social, browser-runtime, auth]
---

# X Account Skill

Use this skill when the user asks to inspect or operate an authenticated X account.
The `x_account` tools are optional and must be enabled with `akvan tools`.

## Safety Rules

- Always call `x_auth_status` before X account actions.
- Never reveal, read aloud, summarize, or copy the contents of the auth state file.
- Never post publicly without explicit user confirmation in the current conversation.
- When calling `x_post`, set `confirmed` to `true` only after the user has approved the exact post text and media.
- If the user wants a post with media, use the `banner-generation` workflow first, then ask for posting confirmation.

## Setup Guidance

If auth is missing, tell the user to run `akvan tools` and create a private
Playwright storage state at `~/.akvan/x/auth.json`. This file must never be
committed to any repository.
