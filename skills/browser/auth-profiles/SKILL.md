---
name: browser-auth-profiles
description: Use Akvan browser tools with named auth profiles for sites like X or GitHub.
version: 2.0.0
author: Akvan Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  akvan:
    tags: [browser, playwright, auth, profiles, x, github, social]
---

# Browser Auth Profiles

Use this skill when the user asks to browse authenticated sites (X, GitHub, etc.).
Browser tools are optional and must be enabled with `akvan tools`
(Browser → enable Browser + Auth profiles).

If `browser_*` tools are missing from the session, tell the user to enable Browser —
do not fall back to raw Playwright via the terminal.

## Setup

1. `akvan tools` → Browser → enable **Browser**
2. **Auth profiles** → Add profile:
   - **Import storage state** (default on VPS/headless; required when sites block Playwright login)
   - **Interactive login** only when a local GUI display is available

Profiles live under `~/.akvan/browser/profiles/<name>/storage_state.json`.
Legacy `~/.akvan/x/auth.json` is auto-migrated to profile `x` when present.

## Safety Rules

- Always call `browser_list_profiles` or `browser_auth_status` before authenticated browsing.
- Never read, summarize, or copy storage state / cookie file contents into chat.
- Prefer `browser_start(profile="…")`, then `browser_snapshot`, then click/type by `@eN` refs.
- Refs are only valid for the latest snapshot — re-snapshot after navigation or interactions.
- Call `browser_close` when done (saves updated cookies back into the profile by default).

## Typical Flow

```text
browser_auth_status(profile="x")
browser_start(profile="x", url="https://x.com/home")
browser_snapshot()
browser_click(ref="e12")   # from snapshot
browser_close()
```

Same pattern for GitHub with `profile="github"`.

## Attach media (images/files)

Use `browser_upload` — click/type alone cannot choose a file:

```text
browser_start(profile="x", url="https://x.com/compose/post")
browser_snapshot()
browser_upload(paths=["~/.akvan/banners/renders/example.png"])
# or: browser_upload(paths=[...], ref="eN")  # click media button + file chooser
browser_snapshot()   # confirm media preview before Post
browser_type(ref="…", text="caption")
# click Post / Control+Enter
browser_close()
```

Prefer paths under the agent vault or `~/.akvan/banners`. The tool reads host files
and sends their bytes to the browser runtime (works with Docker without media mounts).

## VPS / X notes

- On a VPS there is usually no display: create the storage state on a desktop, `scp` it to the server, then **Import**.
- X often blocks automated login windows — import a storage state from a normal browser session.
