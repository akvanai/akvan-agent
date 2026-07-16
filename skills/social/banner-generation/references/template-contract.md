# Reusable banner template contract

Each managed template has exactly this reusable structure:

```text
templates/<template-id>/
├── index.html
├── style.css
└── meta.json
```

Use a lowercase hyphenated template id. HTML owns semantic structure, CSS owns all
presentation, and metadata defines dimensions, fields, and preview data.

## Metadata

```json
{
  "id": "release-card",
  "name": "Release card",
  "description": "Reusable product release announcement",
  "width": 1200,
  "height": 675,
  "fields": [
    {
      "name": "title",
      "type": "text",
      "required": true,
      "description": "Primary headline"
    },
    {
      "name": "eyebrow",
      "type": "text",
      "required": false,
      "default": "NEW"
    }
  ],
  "sample_data": {
    "title": "A reusable release banner",
    "eyebrow": "NEW"
  }
}
```

Field names begin with a letter and contain only letters, digits, and underscores.
Every `{{field_name}}` placeholder in HTML or CSS must be declared in `fields`.
Provide realistic `sample_data` for every required field so the template can always
produce a useful review image.

## HTML and CSS

- Write a complete HTML document with UTF-8 metadata.
- Reference reusable values with `{{field_name}}`.
- Design for the exact metadata width and height.
- Put all styles in `style.css`; reset `html`, `body`, and box sizing.
- Use system fonts, gradients, CSS shapes, and inline markup.
- Do not use JavaScript, remote fonts, remote images, tracking, or network resources.
- Keep important content away from edges and test long realistic sample values.

The renderer substitutes escaped data, disables page JavaScript, blocks browser
requests, captures the configured viewport with Playwright, and stores a PNG under
the managed `renders/` directory.
