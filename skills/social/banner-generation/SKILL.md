---
name: banner-generation
description: Create, inspect, render, review, and refine reusable HTML/CSS banner templates with Akvan banner tools. Use for banner template creation, data-driven banner generation, Playwright screenshots, banner previews, and Telegram review loops.
---

# Banner generation

Use the managed banner tools for the complete workflow. Do not discover or modify
templates with terminal, file, or search tools.

## Boundaries

- Treat `banner_workspace_status` as the authority for banner directories.
- Use only `banner_list_templates`, `banner_get_template`,
  `banner_save_template`, and `banner_render` for template work.
- Never inspect `/opt`, another project, or an arbitrary template directory.
- Keep each reusable template as `index.html`, `style.css`, and `meta.json`.
- Read [references/template-contract.md](references/template-contract.md) before
  creating or updating a reusable template.

## Start every request

1. Call `banner_workspace_status`.
2. Call `banner_list_templates`.
3. Choose the existing-template or template-creation workflow below.

## Render an existing template

1. Call `banner_get_template` for the selected template.
2. Collect values for required metadata fields. Use `sample_data` only for a preview.
3. Call `banner_render` with the template and data.
4. Report the generated PNG path, then follow the review workflow.

## Create or update a reusable template

1. Ask for any missing purpose, reusable fields, size, direction, and visual style.
2. Inspect at most one relevant starter with `banner_get_template` for structure.
3. Prepare complete HTML, CSS, and metadata following the template contract.
4. Summarize the proposed template and ask the user to confirm saving it.
5. After confirmation, call `banner_save_template` with `confirmed: true`. Set
   `overwrite: true` only when the user confirmed replacing that template.
6. Call `banner_render` with `sample_data` to create the first review image.
7. Follow the review workflow. Apply feedback, confirm an overwrite, render again,
   and repeat until the user approves the template.

## Review workflow

- Read `telegram_review` from `banner_workspace_status` or `banner_render`.
- If Telegram is configured and the user requested Telegram review, call
  `telegram_send_file` with the rendered path, a versioned caption, and
  `confirmed: true`.
- If Telegram is configured but delivery was not requested, ask once before sending.
- If Telegram is not configured, tell the user: run `akvan tools`, set up Telegram
  delivery under Social Media, then open the bot and send `/start`. Keep the local
  rendered path available for review; do not search for another delivery service.
- Treat comments received after a sample as revision feedback for that template.

## Defaults

- Use `1200x675` for X landscape banners unless the user requests another size.
- Prefer a managed template over a bundled starter with the same id.
- Escape user data through the banner renderer; do not add scripts or remote assets.
