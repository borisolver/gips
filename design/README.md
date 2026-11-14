# GIPS share preview

`share/gips-v1.png` is an editorial identity card for Grok Improvement Proposals,
an independent project. It is distinct from CleanApp's public report inventory.
It contains no mutable report counts, screenshots, partner logos or claims that
proposed repairs have been implemented.

The 1200 × 630 artwork uses GIPS's existing paper background with dark ink and a
cobalt evidence → proposals → testable outcomes spine. Its main identity remains
readable around 300 × 158. Keep important text inside the existing safe margins.

Regenerate offline using the same Pillow dependency already used by this repo:

```sh
python -m pip install --only-binary=:all: 'Pillow==13.0.0'
python scripts/build_share_preview.py
```

Artwork changes should use a new asset filename and update the template metadata
and `scripts/run_update.py` public-file allowlist together. Do not overwrite an
already shared versioned asset. A new URL helps caches distinguish new artwork;
it does not guarantee a platform refreshes an existing post.

Inter is supplied under the SIL Open Font License in `fonts/OFL.txt`. The font is
vendored so rendering needs neither system fonts nor a network request:

- Source: <https://github.com/google/fonts/tree/0b58fb370093f9a9f4ff785d94405710b79de67c/ofl/inter>
- `Inter.ttf` SHA-256: `29160a80ff49ddcab2c97711247e08b1fab27a484a329ce8b813d820dc559031`

Only the finished PNG is published. Design source, font files and build scripts
remain outside the public Pages artifact.
