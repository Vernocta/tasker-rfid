# Vendored front-end files

These are committed on purpose rather than loaded from a CDN.

SPEC.md §2.4 treats the warehouse network as unreliable — that is why
Postgres runs on the edge device in the first place. A screen mounted by
the dock that loses its layout and typography the moment the wifi drops
would be the one part of the system that is not local-first. So the two
things the browser needs are served from this machine.

| File | What it is | Version |
|---|---|---|
| `tailwind-3.4.17.js` | Tailwind's browser build, the same one `cdn.tailwindcss.com` serves | 3.4.17 |
| `fonts.css` + `fonts/` | Archivo and Public Sans, latin and latin-ext | from Google Fonts |

Nothing here is compiled. Tailwind reads the classes in the page and makes
the CSS in the browser, exactly as it did from the CDN — only the file now
comes from us.

## Refreshing them

Only needed to take a newer Tailwind or add a font weight.

```bash
# Tailwind, pinned to a version rather than "latest"
curl -sL https://cdn.tailwindcss.com/3.4.17 \
  -o src/tasker_rfid/web/static/vendor/tailwind-3.4.17.js

# then update the <script src> in web/templates/base.html to match
```

For the fonts: fetch the stylesheet from Google with a browser user-agent
(so it returns woff2), download each `.woff2` it names into `fonts/`, and
rewrite the `src:` URLs in `fonts.css` to point at `/static/vendor/fonts/`.
Keep the `unicode-range` lines — they are what stops the browser loading
the Cyrillic and Greek files to render Spanish.
