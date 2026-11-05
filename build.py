"""Build the static GIPS page from the editable proposal and template sources."""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROPOSALS = json.loads((ROOT / "proposals.json").read_text())


def esc(value):
    return html.escape(str(value), quote=True)


def images(proposal, eager=False):
    media = [e for e in proposal["evidence"] if e.get("image")]
    if proposal["id"] == "GIP-04":
        media.reverse()
    parts = []
    for i, e in enumerate(media):
        loading = "eager" if eager else "lazy"
        priority = ' fetchpriority="high"' if eager and i == 0 else ""
        parts.append(
            f'<a class="evidence-image" href="{esc(e["image"])}" '
            f'data-caption="{esc(proposal["caption"])}" '
            f'data-source="{esc(e["url"])}" data-label="{esc(e["label"])}" '
            f'aria-label="Enlarge: {esc(e["label"])}">'
            f'<img src="{esc(e["image"])}" alt="{esc(e["label"])} — original report image" '
            f'width="{e["width"]}" height="{e["height"]}" '
            f'loading="{loading}" decoding="async"{priority}>'
            '<span class="expand" aria-hidden="true">↗</span></a>'
        )
    return f'<div class="evidence-gallery count-{len(media)}">' + "".join(parts) + "</div>"


def sources(proposal):
    rows = []
    for e in proposal["evidence"]:
        date = e.get("source_date") or e.get("recorded_date")
        date_label = "Published" if e.get("source_date") else "Recorded"
        date_html = f'{date_label} <time datetime="{date}">{date}</time>' if date else ""
        kind = "Image report" if e.get("image") else "Report"
        rows.append(
            f'<li><a href="{esc(e["url"])}" target="_blank" rel="noopener noreferrer">'
            f'{esc(e["label"])} <span aria-hidden="true">↗</span></a>'
            f'<small>{kind} · {date_html}</small></li>'
        )
    count = len(rows)
    return (
        f'<details class="sources"><summary>{count} source report{"s" if count != 1 else ""}'
        '<span aria-hidden="true">＋</span></summary><ul>' + "".join(rows) + "</ul></details>"
    )


def copy(proposal):
    return (
        f'<div class="proposal-copy"><span class="eyebrow">{esc(proposal["id"])}</span>'
        f'<h3>{esc(proposal["title"])}</h3>'
        f'<p class="fix">{esc(proposal["fix"])}</p>'
        f'<p class="metric"><span>Measure</span> {esc(proposal["metric"])}</p>'
        f'{sources(proposal)}</div>'
    )


def visual_proposal(proposal, featured=False):
    return (
        f'<article class="proposal {"featured" if featured else "reveal"}" id="{proposal["id"].lower()}">'
        f'{images(proposal, eager=featured)}{copy(proposal)}</article>'
    )


visual = [p for p in PROPOSALS if any(e.get("image") for e in p["evidence"])]
text_only = [p for p in PROPOSALS if not any(e.get("image") for e in p["evidence"])]
replacements = {
    "{{FEATURED}}": visual_proposal(visual[0], featured=True),
    "{{VISUAL_PROPOSALS}}": "".join(visual_proposal(p) for p in visual[1:]),
    "{{TEXT_PROPOSALS}}": "".join(
        f'<article class="text-proposal reveal" id="{p["id"].lower()}">{copy(p)}</article>'
        for p in text_only
    ),
    "{{PROPOSAL_COUNT}}": str(len(PROPOSALS)),
    "{{REPORT_COUNT}}": str(sum(len(p["evidence"]) for p in PROPOSALS)),
    "{{IMAGE_COUNT}}": str(sum(bool(e.get("image")) for p in PROPOSALS for e in p["evidence"])),
    "{{TEXT_PROPOSAL_COUNT}}": str(len(text_only)),
}
page = (ROOT / "template.html").read_text()
for key, value in replacements.items():
    page = page.replace(key, value)
(ROOT / "index.html").write_text(page)
(ROOT / "gips.json").write_text(json.dumps([
    {
        "id": p["id"], "title": p["title"], "fix": p["fix"], "metric": p["metric"],
        "evidence": [e["id"] for e in p["evidence"]],
        "images": [e["image"] for e in p["evidence"] if e.get("image")],
    } for p in PROPOSALS
], indent=2, ensure_ascii=False) + "\n")
print(f'Built {len(PROPOSALS)} proposals with {replacements["{{IMAGE_COUNT}}"]} original evidence images.')
