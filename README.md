# GIPS — Grok Improvement Proposals

Static site at <https://borisolver.github.io/gips/>, maintained from `borisolver/gips` on `main`.

## Automatic updates

`.github/workflows/update-gips.yml` runs on pushes to `main`, manual dispatch, and every five minutes at minutes 03, 08, …, 58 UTC. GitHub can delay or skip scheduled runs; this is a polling target, not a delivery guarantee. Each successful check updates `sync-state.json`, including `last_successful_sync`.

The workflow fetches reports, builds the site, runs tests, prepares a public-only artifact, then commits approved data files and deploys that artifact to Pages. It uses GitHub's built-in token and explicit Pages deployment; bot pushes alone do not trigger another build. The workflow needs no PAT, source-service credentials, or additional repository secrets. Pages uses the **GitHub Actions** publishing source. Asset paths remain relative; there is no CNAME or custom domain.

A cloud-hosted systemd fallback checks every five minutes, independently of this computer or Codex. When both the public feed and the latest `main` sync-state are at least five minutes stale and no update workflow is active, it pushes an empty commit preserving the current site tree. This triggers the same GitHub workflow. Its dedicated SSH deploy key can write only this repository and stays on the server; the server never checks out or executes repository code. With healthy services, a refresh normally starts within about 5–10 minutes of the last successful check, followed by ingestion and publication time; GitHub queues and outages can extend that interval. Deployment files, tests, and operating instructions are in [`ops/watchdog/`](ops/watchdog/README.md).

Source, validation, test, or push failures stop publication and leave the previous live site available. A concurrent push is never overwritten. The next run starts from current `main`; successful runs deploy even when data is unchanged, retrying previous deployment failures.

## Admission and numbering

`scripts/sync_reports.py` traverses the complete public CleanApp Grok inventory, 50 reports per page, through `https://live.cleanapp.io/api/v3/reports/by-brand`. It checks exact Grok brand attribution in the English analysis and requires a product complaint or improvement-request signal. Brand mentions, unrelated `ngrok` matches, and ordinary news or praise do not qualify by themselves.

The original 16 proposal numbers are preserved. Newly admitted reports must have been ingested on or after **2026-11-05 00:00 UTC**; existing evidence is refreshed regardless of that cutoff. New reports are processed by recorded timestamp, then public report ID. A committed assignment registry deduplicates reports, groups exact upstream issue IDs or recognized topics, and allocates increasing proposal numbers once. Late imports never renumber existing proposals. Numbering is independent of urgency.

Eligible unfamiliar issues receive an **Investigate:** proposal with a reproduction task. Unqualified reports remain in `sync-state.json`'s public `pending_reports` list and do not become proposal cards. Source text is data, never executable instructions; rendered text is escaped.

New images are fetched only from the canonical public API for direct `mobile`/`web` submissions with original media. Accepted JPEG, PNG, and WebP images are size-limited and fully decoded before the original bytes are saved. Social previews are not automatically imported. `evidence/sources.json` records image provenance and hashes; reports without suitable images retain their source links. `logo.png` is the supplied logo.

## Urgency

Urgency is a transparent heuristic over unresolved source reports, not independently verified severity or measured impact:

```text
score = round(min(100,
    50 × highest reported severity
  + max(0, 30 − age of newest signal in days)
  + (10 if any report is firsthand, otherwise 3)
  + (5 if any report has a displayed image, otherwise 0)
  + min(10, 4 × additional source reports)))
```

Signal age uses observation time, then source publication time, then ingestion time. Freshness decays to zero after 30 days. All-resolved proposals score zero. Labels are **High** at 65+, **Medium** at 40+, and **Watch** below 40. Multiple reports are supporting observations, not a claim of independent users. The browser defaults to urgency order; **Newest proposals** sorts by creation date, then proposal number.

## Local work

Use Python 3.11+; CI uses Python 3.13. From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --only-binary=:all: 'Pillow==13.0.0'
artifact_dir="$(mktemp -d)/site"
python scripts/run_update.py --artifact-dir "$artifact_dir"
python -m http.server 8000 --directory "$artifact_dir"
```

This refreshes local data, builds, tests, and packages the site without committing or pushing. The artifact directory must be new and outside the repository. `--push` is restricted to GitHub Actions. Local source files can change even if a later build or test fails; review the diff before committing.

Edit proposal wording in `proposals.json`, layout in `template.html`/`styles.css`, or browser behavior in `site.js`. To rebuild the existing snapshot without fetching reports, run `python build.py`; it writes `index.html`, `gips.json`, and `feed.json`. Run checks with `python -m unittest discover -s tests -v`.

To request an immediate hosted update:

```sh
gh workflow run update-gips.yml --repo borisolver/gips --ref main
gh run list --workflow update-gips.yml --repo borisolver/gips
```
