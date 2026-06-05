# Per-company logo overrides — the guaranteed-correct cover logo

Drop a verified logo file here and the pitch-pack cover for that company will use
it **verbatim, offline, every time** — no web lookup, no guessing, no chance of a
wrong logo. This is the unequivocal way to lock the correct logo for any account
Sara pitches.

## How to add a logo

1. Save the company's real logo as a file in **this folder**.
2. Name the file with the company's *slug* — its name in lowercase with every
   space and punctuation removed — plus the image extension:

   | Company                    | Slug                      | File to drop                     |
   | -------------------------- | ------------------------- | -------------------------------- |
   | Diageo                     | `diageo`                  | `diageo.svg` / `diageo.png`      |
   | Oxford Quantum Circuits    | `oxfordquantumcircuits`   | `oxfordquantumcircuits.svg`      |
   | M&S                        | `ms`                      | `ms.png`                         |
   | L'Oréal                    | `loral`                   | `loral.png`                      |

   To check a slug: `python -c "from tool.company_logos import slugify; print(slugify('Your Company'))"`

3. That's it. The next pack generated for that company uses the file.

## Accepted formats (best first)

`.svg` → `.png` → `.webp` → `.jpg` / `.jpeg` → `.gif`

If more than one extension exists for the same slug, the first in that order wins.

## Tips for a clean cover

- **Prefer SVG** (sharp at any size).
- Use a logo with a **transparent or white background** and **dark/colour ink** —
  the cover places the logo on a white area, so a white-only ("reversed") logo
  would vanish. Use the standard, not the reversed, version.
- A tightly-cropped logo looks best; the cover trims surrounding padding for you.

## Aliases

A file named for any *registered alias* also works (aliases live in
`tool/company_logos.py`). For example `british gas` resolves to the `centrica`
entry, so `centrica.png` covers it. When in doubt, name the file for the exact
company string used to generate the pack.

## When no file is here

The generator falls back to the curated domain registry (pins the company's
official domain so the auto-resolver fetches the *right* company's logo) and,
failing that, prints the company name as a clean typographic wordmark. It will
**never** substitute a different company's logo.
