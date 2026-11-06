"""Render the static GIPS snapshot and its same-origin browser update feed."""
import argparse
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


def esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def safe_url(value):
    value = str(value or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme:
        return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""
    return value if value and not value.startswith(("//", "/", "\\")) and "\\" not in value else ""


def number(proposal):
    return int(proposal.get("number") or re.search(r"\d+$", proposal["id"]).group())


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return 0


def formatted_time(value):
    if not value or not timestamp(value):
        return "Awaiting first check"
    return datetime.fromtimestamp(timestamp(value), timezone.utc).strftime("%d %b %Y, %H:%M UTC")


def urgency_key(proposal):
    return (-float(proposal.get("priority_score") or 0), -timestamp(proposal.get("updated_at")), proposal["id"])


def media_for(proposal):
    return [e for e in proposal.get("evidence", []) if safe_url(e.get("image"))]


def images(proposal, eager=False, first_only=False):
    media = media_for(proposal)
    if proposal["id"] == "GIP-04":
        media = list(reversed(media))
    if first_only:
        media = media[:1]
    if not media:
        return ""
    parts = []
    for i, evidence in enumerate(media):
        image = safe_url(evidence["image"])
        label = evidence.get("label") or proposal["title"]
        caption = evidence.get("caption") or proposal.get("caption") or label
        dimensions = ""
        if evidence.get("width") and evidence.get("height"):
            dimensions = f' width="{int(evidence["width"])}" height="{int(evidence["height"])}"'
        priority = ' fetchpriority="high"' if eager and i == 0 else ""
        parts.append(
            f'<a class="evidence-image" href="{esc(image)}" '
            f'data-caption="{esc(caption)}" data-source="{esc(safe_url(evidence.get("url")))}" '
            f'data-label="{esc(label)}" aria-label="Enlarge: {esc(label)}">'
            f'<img src="{esc(image)}" alt="{esc(label)} — original report image"{dimensions} '
            f'loading="{"eager" if eager else "lazy"}" decoding="async"{priority}>'
            '<span class="expand" aria-hidden="true">↗</span></a>'
        )
    return f'<div class="evidence-gallery count-{len(media)}">' + "".join(parts) + "</div>"


def sources(proposal):
    rows = []
    for evidence in proposal.get("evidence", []):
        url = safe_url(evidence.get("url"))
        if not url:
            continue
        date = evidence.get("source_date") or evidence.get("recorded_date")
        date_label = "Published" if evidence.get("source_date") else "Recorded"
        date_html = f' · {date_label} <time datetime="{esc(date)}">{esc(str(date)[:10])}</time>' if date else ""
        kind = "Image report" if safe_url(evidence.get("image")) else "Report"
        rows.append(
            f'<li><a href="{esc(url)}" target="_blank" rel="noopener noreferrer">'
            f'{esc(evidence.get("label") or evidence.get("id") or "Source report")} <span aria-hidden="true">↗</span></a>'
            f'<small>{kind}{date_html}</small></li>'
        )
    if not rows:
        return ""
    return (
        f'<details class="sources"><summary>{len(rows)} source report{"s" if len(rows) != 1 else ""}'
        '<span aria-hidden="true">＋</span></summary><ul>' + "".join(rows) + "</ul></details>"
    )


def priority(proposal, rank):
    label = proposal.get("priority_label") or "Unranked"
    reason = str(proposal.get("priority_reason") or "").strip()
    label_class = re.sub(r"[^a-z]", "", label.lower())
    return (
        f'<div class="priority-line"><span class="priority-label priority-{label_class}">{esc(label)} priority</span>'
        f'<span class="priority-rank">Urgency #{rank}</span></div>'
        + (f'<p class="priority-reason">{esc(reason)}</p>' if reason else "")
    )


def render_proposal(proposal, rank):
    anchor = proposal["id"].lower()
    media = images(proposal)
    metric = str(proposal.get("metric") or "").strip()
    fix = str(proposal.get("fix") or "").strip()
    content = (
        f'<article class="proposal {"has-media" if media else "text-proposal"} reveal" id="{esc(anchor)}" '
        f'data-proposal-id="{esc(proposal["id"])}" data-number="{number(proposal)}" '
        f'data-created-at="{esc(proposal.get("created_at", ""))}" data-updated-at="{esc(proposal.get("updated_at", ""))}" '
        f'data-priority-score="{float(proposal.get("priority_score") or 0)}">'
        f'<div class="proposal-topline"><a class="eyebrow proposal-number" href="#{esc(anchor)}">GIPS #{number(proposal):03d}</a>'
        f'{priority(proposal, rank)}</div>{media}<div class="proposal-copy">'
        f'<h3>{esc(proposal["title"])}</h3>'
        + (f'<p class="fix">{esc(fix)}</p>' if fix else "")
        + (f'<p class="metric"><span>Measure</span> {esc(metric)}</p>' if metric else "")
        + sources(proposal) + '</div></article>'
    )
    digest = hashlib.sha256(content.encode()).hexdigest()
    return content.replace('<article ', f'<article data-content-hash="{digest}" ', 1), digest


def render_spotlight(proposal, rank=1):
    if not proposal:
        return '<div class="priority-spotlight-content"><p>New proposals will appear here.</p></div>'
    label = proposal.get("priority_label") or "Unranked"
    return (
        '<div class="priority-spotlight-content"><p class="eyebrow proof-label">Evidence spotlight</p>'
        + images(proposal, eager=True, first_only=True)
        + f'<div class="spotlight-copy"><p class="eyebrow">GIPS #{number(proposal):03d} · {esc(label)} priority · Urgency #{rank}</p>'
        f'<h2><a href="#{esc(proposal["id"].lower())}">{esc(proposal["title"])}</a></h2>'
        f'<p>{esc(proposal.get("fix") or proposal.get("priority_reason") or "")}</p>'
        f'<a class="spotlight-link" href="#{esc(proposal["id"].lower())}">See the proposal <span aria-hidden="true">↓</span></a>'
        '</div></div>'
    )


def build(output_dir=ROOT):
    proposals = json.loads((ROOT / "proposals.json").read_text())
    state_path = ROOT / "sync-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    ordered = sorted(proposals, key=urgency_key)
    counts = {
        "proposals": len(proposals),
        "reports": len({e["id"] for p in proposals for e in p.get("evidence", [])}),
        "images": len({safe_url(e.get("image")) for p in proposals for e in p.get("evidence", []) if safe_url(e.get("image"))}),
    }
    items = []
    for rank, proposal in enumerate(ordered, 1):
        markup, digest = render_proposal(proposal, rank)
        items.append({
            "id": proposal["id"], "number": number(proposal),
            "created_at": proposal.get("created_at", ""), "updated_at": proposal.get("updated_at", ""),
            "priority_score": float(proposal.get("priority_score") or 0),
            "html": markup, "html_hash": digest,
        })
    spotlight_proposal = next((p for p in ordered if media_for(p)), ordered[0] if ordered else None)
    spotlight_rank = ordered.index(spotlight_proposal) + 1 if spotlight_proposal else 1
    spotlight = render_spotlight(spotlight_proposal, spotlight_rank)
    spotlight_hash = hashlib.sha256(spotlight.encode()).hexdigest()
    version_input = json.dumps({"items": items, "state": state, "spotlight": spotlight}, sort_keys=True, ensure_ascii=False)
    version = hashlib.sha256(version_input.encode()).hexdigest()
    checked_at = state.get("last_successful_sync") or ""
    feed = {"version": version, "checked_at": checked_at, "counts": counts, "items": items, "spotlight_html": spotlight, "spotlight_hash": spotlight_hash}
    replacements = {
        "{{SPOTLIGHT}}": spotlight,
        "{{SPOTLIGHT_HASH}}": spotlight_hash,
        "{{PROPOSALS}}": "".join(item["html"] for item in items),
        "{{PROPOSAL_COUNT}}": str(counts["proposals"]),
        "{{REPORT_COUNT}}": str(counts["reports"]),
        "{{IMAGE_COUNT}}": str(counts["images"]),
        "{{FEED_VERSION}}": version,
        "{{CHECKED_AT}}": esc(checked_at),
        "{{CHECKED_LABEL}}": esc(formatted_time(checked_at)),
    }
    page = (ROOT / "template.html").read_text()
    for key, value in replacements.items():
        page = page.replace(key, value)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(page)
    (output_dir / "feed.json").write_text(json.dumps(feed, ensure_ascii=False, separators=(",", ":")) + "\n")
    (output_dir / "gips.json").write_text(json.dumps([
        {
            "id": p["id"], "number": number(p), "title": p["title"], "fix": p.get("fix", ""), "metric": p.get("metric", ""),
            "created_at": p.get("created_at", ""), "updated_at": p.get("updated_at", ""),
            "priority_score": float(p.get("priority_score") or 0),
            "priority_label": p.get("priority_label", ""), "priority_reason": p.get("priority_reason", ""),
            "evidence": [e["id"] for e in p.get("evidence", [])],
            "images": [e["image"] for e in media_for(p)],
        } for p in ordered
    ], indent=2, ensure_ascii=False) + "\n")
    return feed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    args = parser.parse_args()
    result = build(args.output_dir)
    print(f'Built {result["counts"]["proposals"]} proposals with {result["counts"]["images"]} evidence images.')
