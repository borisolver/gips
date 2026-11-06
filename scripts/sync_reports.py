"""Reconcile public Grok reports into a durable, sequential proposal registry.

No model calls, credentials, private records, or instructions from report text.
Numbers are allocated once; urgency is recomputed independently on each run.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://live.cleanapp.io/api/v3/reports/"
SOURCE = "https://www.cleanapp.io/digital/report/"
ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ID = re.compile(r"^rpt_[A-Za-z0-9_-]{12,80}$")
CUTOFF = "2026-11-05T00:00:00Z"
LEGACY_CREATED = "2026-11-05T08:02:00Z"
MAX_BYTES = 12 * 1024 * 1024

# Deliberately narrow routing rules. Unmatched issues get separate IDs; a new
# topic is never silently folded into an unrelated proposal by word overlap.
CATALOG = {
    "data-loss": (None, "Protect work and make recovery possible.",
        "Require explicit confirmation for destructive actions, keep recoverable versions, and offer a clear restore path.",
        "Prevented data loss and successful restores", "Potential loss of user work"),
    "account-linking": (None, "Make X-to-Grok setup work in one flow.",
        "Carry the signed-in account through one clear linking step, preserve progress, and recover from setup errors.",
        "Successful account setup", "Account setup blocks access"),
    "x-posting": (None, "Turn a posting request into a permissioned action.",
        "Offer a preview-and-confirm posting action for the connected X account, or a direct handoff that preserves the draft.",
        "Completed posting requests", "A requested action stops at a handoff"),
    "product-identity": (None, "Make Grok and Grok Bot easy to tell apart.",
        "Explain each app’s role at discovery and guide people directly to the one that can complete their task.",
        "First-attempt task completion", "Unclear product roles create friction"),
    "first-answer": ("GIP-01", None, None, None, "First answer blocked"),
    "handoff": ("GIP-02", None, None, None, "Task handoff breaks"),
    "role-grounding": ("GIP-03", None, None, None, "Unverified identity claims"),
    "image-delivery": ("GIP-04", None, None, None, "Promised output fails"),
    "voice": ("GIP-05", None, None, None, "Voice sessions degrade"),
    "billing": ("GIP-06", None, None, None, "Paid access is affected"),
    "developer": ("GIP-07", None, None, None, "Developer integrations are affected"),
    "tracking": ("GIP-08", None, None, None, "Reports need a tracking path"),
    "memory": ("GIP-09", None, None, None, "Session continuity is affected"),
    "errors": ("GIP-10", None, None, None, "Errors need a recovery path"),
    "links": ("GIP-11", None, None, None, "An outbound link is misleading"),
    "landscape": ("GIP-12", None, None, None, "Wide answers are hard to read"),
    "clipping": ("GIP-13", None, None, None, "Visible animation defects"),
    "standalone": ("GIP-14", None, None, None, "Standalone context is missing"),
    "capability": ("GIP-15", None, None, None, "Capability changes need explanation"),
    "bot-setup": ("GIP-16", None, None, None, "Bot setup and controls fail"),
}


def parse_time(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    except ValueError:
        return None


def iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean(value, limit=600):
    return " ".join(str(value or "").split())[:limit]


def validate_api_url(url):
    parsed = urllib.parse.urlsplit(url)
    allowed_paths = {"/api/v3/reports/by-brand", "/api/v3/reports/rawimage/by-public-id"}
    if parsed.scheme != "https" or parsed.netloc != "live.cleanapp.io" or parsed.path.rstrip("/") not in allowed_paths:
        raise ValueError("Unapproved public API URL")


class PublicAPIRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_api_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def get_bytes(url):
    validate_api_url(url)
    opener = urllib.request.build_opener(PublicAPIRedirectHandler())
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "GIPS-public-feed/1.0", "Accept": "application/json, image/*"})
            with opener.open(request, timeout=35) as response:
                validate_api_url(response.url)
                data = response.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise ValueError("API response exceeds size limit")
                return data
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError("Public API request failed")


def fetch_inventory():
    """Full offset traversal: recent sorting is source time, not import time."""
    rows, seen, offset = [], set(), 0
    for _ in range(200):
        query = urllib.parse.urlencode({"brand_name": "Grok", "n": 50, "offset": offset,
            "lang": "en", "full_data": "false", "kind": "all", "sort": "recent"})
        page = json.loads(get_bytes(API + "by-brand?" + query))
        batch = page.get("reports")
        if not isinstance(batch, list) or not isinstance(page.get("has_more"), bool):
            raise ValueError("Malformed public feed page; previous snapshot retained")
        if page.get("offset", offset) != offset or (page["has_more"] and not batch):
            raise ValueError("Incomplete public feed pagination")
        for row in batch:
            public_id = row.get("report", {}).get("public_id", "")
            if not PUBLIC_ID.fullmatch(public_id):
                raise ValueError("Invalid public report ID")
            if public_id not in seen:
                rows.append(row)
                seen.add(public_id)
        offset += len(batch)
        if not page["has_more"]:
            if not rows:
                raise ValueError("Unexpected empty public feed; previous snapshot retained")
            return rows
    raise ValueError("Public feed exceeded pagination limit")


def normalize_report(row):
    raw, provenance = row.get("report", {}), row.get("evidence", {})
    public_id = raw.get("public_id", "")
    if not PUBLIC_ID.fullmatch(public_id):
        raise ValueError("Invalid public report ID")
    analysis = next((a for a in row.get("analysis", []) if a.get("language") == "en"), None)
    if not analysis or clean(analysis.get("brand_name")).casefold() != "grok":
        return None
    recorded = parse_time(provenance.get("ingested_at")) or parse_time(raw.get("timestamp"))
    if not recorded or not clean(analysis.get("title")):
        raise ValueError("Public report lacks a valid timestamp or title")
    source = parse_time(provenance.get("source_published_at"))
    observed = parse_time(provenance.get("observed_at"))
    severity = analysis.get("severity_level", 0.35)
    if not isinstance(severity, (int, float)) or not math.isfinite(severity):
        severity = 0.35
    return {
        "id": public_id, "label": clean(analysis["title"], 160), "url": SOURCE + public_id,
        "summary": clean(analysis.get("summary"), 1200), "excerpt": clean(provenance.get("excerpt"), 1200),
        "recorded_at": iso(recorded), "recorded_date": iso(recorded)[:10],
        "source_date": iso(source)[:10] if source else "", "source_published_at": iso(source) if source else None,
        "signal_at": iso(observed or source or recorded),
        "severity": round(max(0, min(1, severity)), 3),
        "report_kind": clean(provenance.get("report_kind") or "unknown", 30),
        "intake_channel": clean(provenance.get("intake_channel"), 30),
        "has_original_media": provenance.get("has_original_media") is True,
        "resolution_status": clean(provenance.get("resolution_status") or "unknown", 30),
        "validation_status": clean(provenance.get("validation_status") or "not_independently_reproduced", 60),
        "issue_id": clean(row.get("recurrence", {}).get("issue_id"), 100),
        "image": None,
    }


def topic_for(report):
    text = " ".join(report.get(k, "") for k in ("label", "summary", "excerpt")).casefold()
    has = lambda pattern: re.search(pattern, text) is not None
    if has(r"\b(lost|loses|loss|erased|wiped|deleted|deletes)\b|(?:accidental|unexpected|unintended).{0,15}delet|delet\w*.{0,35}without.{0,15}(?:consent|confirm|permission)") and has(r"\b(work|data|history|conversations?|files?)\b"):
        return "data-loss"
    if has(r"(post|send)\w*.{0,60}(message|tweet|hello world|x account)") and has(r"(refus|cannot|can't|unable|redirect)"):
        return "x-posting"
    if has(r"onboard|account.{0,30}(setup|set.up|link)|link.{0,20}account|setting up.{0,20}account"):
        return "account-linking"
    if has(r"grok.?bot") and has(r"relationship|differentiation|dominant assistant|naming|tell.{0,12}apart"):
        return "product-identity"
    if has(r"sign.up|paywall|upsell|auth.wall") and has(r"answer|query|first.turn"):
        return "first-answer"
    if has(r"1:1|handoff|deep.link") and has(r"link|redirect|switch"):
        return "handoff"
    if has(r"(ceo|executive|affiliation|integration).{0,60}(hallucin|false|misread|wrong)|hallucin.{0,50}(ceo|role|executive)"):
        return "role-grounding"
    if has(r"phantom.image|no.image|image.{0,30}(missing|deliver)|lettering|text.in.image|render.failure"):
        return "image-delivery"
    if has(r"voice") and has(r"degrad|reset|quality|audio|latency"):
        return "voice"
    if has(r"refund|entitlement|paid.{0,40}(access|service)|purchase.{0,30}(missing|fail)"):
        return "billing"
    if has(r"deprecat|model.retire|api.{0,30}(stability|retirement)"):
        return "developer"
    if has(r"bug.{0,40}(tracking|escalat)|report.{0,25}tracking"):
        return "tracking"
    if has(r"memory|persona") and has(r"retain|persist|consisten|swing"):
        return "memory"
    if has(r"unexpected.content.type"):
        return "errors"
    if has(r"redirect") and has(r"unrelated|third.party.paid|external.site"):
        return "links"
    if has(r"landscape|auto.rotate"):
        return "landscape"
    if has(r"clipping|hands.{0,20}(through|clothes)|collision"):
        return "clipping"
    if has(r"standalone") and has(r"explain|context|unimpressive"):
        return "standalone"
    if has(r"image.tool.restrict|capability.change"):
        return "capability"
    if has(r"grok.?bot") and has(r"api.key|built.in.skills|desktop.{0,20}mobile|parity"):
        return "bot-setup"
    return None


def is_candidate(report):
    """A brand mention alone does not turn news or praise into a product repair."""
    text = " ".join(report.get(k, "") for k in ("label", "summary", "excerpt")).casefold()
    if not re.search(r"\bgrok\b|\bgrokbot\b", text):
        return False  # Includes the brand classifier's occasional ngrok matches.
    issue = re.search(
        r"\b(fail\w*|bug\w*|broken|crash\w*|errors?|unable|cannot|missing|confus\w*|refus\w*|friction|unresponsive|inconsistent|degrad\w*|problems?|issues?|unexpected|unusable|lost|loses|loss|deleted|deletes|erased|wiped|unreliable|mislead\w*|hallucinat\w*|clipping|blocked|stuck|disrupt\w*)\b"
        r"|\b(can['’]t|doesn['’]t|won['’]t)\b|\bnot (?:work\w*|clear|available|possible)\b"
        r"|\b(?:feature request|wish (?:grok|it)|should (?:allow|support|offer|show|preserve)|needs? (?:a|an|to|better))\b",
        text,
    )
    if not issue:
        return False
    if report.get("intake_channel") in ("mobile", "web"):
        return True
    if topic_for(report):
        return True
    if report.get("report_kind") == "firsthand" and issue:
        return True
    # Unmatched third-party coverage needs triage instead of generating dozens
    # of duplicate proposals from headlines about the same event.
    return bool(issue and report.get("excerpt") and report.get("report_kind") != "coverage")


def rank_proposal(proposal, now):
    evidence = proposal["evidence"]
    active = [e for e in evidence if e.get("resolution_status") not in ("resolved", "fixed", "closed")]
    relevant = active or evidence
    severity = max((e.get("severity", 0.35) for e in relevant), default=0.35)
    dates = [parse_time(e.get("signal_at") or e.get("source_date") or e.get("recorded_date")) for e in relevant]
    dates = [d for d in dates if d and d <= now]
    latest = max(dates) if dates else now
    age = max(0, (now - latest).total_seconds() / 86400)
    freshness = max(0, 30 * (1 - age / 30))
    firsthand = any(e.get("report_kind") == "firsthand" for e in relevant)
    original = any(e.get("image") for e in relevant)
    # Repeated reports are supporting observations, never asserted independent users.
    support = min(10, 4 * max(0, len(relevant) - 1))
    score = round(min(100, severity * 50 + freshness + (10 if firsthand else 3) + (5 if original else 0) + support))
    if not active:
        score = 0
    topic = proposal.get("topic")
    reason = CATALOG.get(topic, (None, None, None, None, "New product issue"))[4]
    if age <= 2:
        reason += " · reported in the last 48 hours"
    elif age <= 7:
        reason += " · reported this week"
    if len(relevant) > 1:
        reason += f" · {len(relevant)} source reports"
    proposal.update(priority_score=score, priority_label="High" if score >= 65 else "Medium" if score >= 40 else "Watch",
        priority_reason=reason if active else "Sources record this issue as resolved",
        priority_components={"severity": round(severity * 50, 1), "freshness": round(freshness, 1),
            "firsthand": 10 if firsthand else 3, "image": 5 if original else 0, "support": support},
        status="proposed" if active else "resolved")


def ingest_reports(proposals, state, rows, now):
    """Pure reconciliation: safe to repeat; numbering never depends on urgency."""
    proposals, state = copy.deepcopy(proposals), copy.deepcopy(state)
    assignments = state.setdefault("assignments", {})
    issues = state.setdefault("issue_proposals", {})
    by_id = {p["id"]: p for p in proposals}
    if len(by_id) != len(proposals):
        raise ValueError("Duplicate proposal IDs")
    by_report = {}
    topics = {}
    for p in proposals:
        p.setdefault("number", int(p["id"].split("-")[-1]))
        p.setdefault("created_at", LEGACY_CREATED)
        p.setdefault("updated_at", p["created_at"])
        if "topic" not in p:
            p["topic"] = next((key for key, value in CATALOG.items() if value[0] == p["id"]), "")
        if p["topic"]:
            topics[p["topic"]] = p["id"]
        for e in p["evidence"]:
            if e["id"] in by_report:
                raise ValueError("A report cannot belong to multiple proposals")
            by_report[e["id"]] = (p, e)
            if assignments.get(e["id"], p["id"]) != p["id"]:
                raise ValueError("Report assignment changed unexpectedly")
            assignments[e["id"]] = p["id"]
            if e.get("issue_id"):
                issues.setdefault(e["issue_id"], p["id"])
    next_number = max([state.get("next_number", 1) - 1] + [p["number"] for p in proposals]) + 1
    normalized = [r for row in rows if (r := normalize_report(row)) is not None]
    if not normalized:
        raise ValueError("No valid Grok records in public inventory")
    normalized.sort(key=lambda r: (r["recorded_at"], r["id"]))
    seen, pending, added, created = set(), [], 0, 0
    for report in normalized:
        rid = report["id"]
        if rid in seen:
            continue
        seen.add(rid)
        if parse_time(report["recorded_at"]) > now:
            raise ValueError("Feed contains a future timestamp")
        if rid in by_report:
            proposal, existing = by_report[rid]
            preserved = {k: existing[k] for k in ("image", "width", "height", "image_omitted_reason") if k in existing}
            existing.update(report)
            existing.update(preserved)
            if report["issue_id"]:
                issues.setdefault(report["issue_id"], proposal["id"])
            continue
        if parse_time(report["recorded_at"]) < parse_time(state.get("feed_cutoff", CUTOFF)):
            continue
        if not is_candidate(report):
            pending.append({"id": rid, "url": report["url"], "label": report["label"],
                "reason": "Needs product relevance or evidence review"})
            continue
        topic = topic_for(report)
        pid = assignments.get(rid) or issues.get(report["issue_id"]) or topics.get(topic)
        if pid and pid not in by_id:
            raise ValueError("Assignment points to a missing proposal")
        if not pid:
            pid = f"GIP-{next_number:02d}"
            catalog = CATALOG.get(topic)
            proposal = {"id": pid, "number": next_number, "topic": topic or "",
                "title": catalog[1] if catalog and catalog[1] else "Investigate: " + clean(report["label"], 100),
                "fix": catalog[2] if catalog and catalog[2] else "Reproduce the reported behavior, identify the failing step, and add a regression check with the repair.",
                "metric": catalog[3] if catalog and catalog[3] else "Successful completion of the reported task",
                "caption": "Reported experience: " + clean(report["label"], 200),
                "created_at": iso(now), "updated_at": report["recorded_at"],
                "first_report_at": report["recorded_at"],
                "first_ingested_at": iso(now), "evidence": []}
            proposals.append(proposal)
            by_id[pid] = proposal
            if topic:
                topics[topic] = pid
            next_number += 1
            created += 1
        proposal = by_id[pid]
        proposal["evidence"].append(report)
        proposal["updated_at"] = max(proposal["updated_at"], report["recorded_at"])
        by_report[rid] = (proposal, report)
        assignments[rid] = pid
        if report["issue_id"]:
            issues.setdefault(report["issue_id"], pid)
        added += 1
    for p in proposals:
        rank_proposal(p, now)
    state.update(schema_version=1, feed_cutoff=state.get("feed_cutoff", CUTOFF), next_number=next_number,
        last_successful_sync=iso(now), newest_report_at=max((r["recorded_at"] for r in normalized), default=None),
        source_inventory_count=len(seen), proposal_count=len(proposals), report_count=len(by_report),
        update_interval_minutes=5, pending_reports=pending)
    return proposals, state, {"new_reports": added, "new_proposals": created}


def collect_media(proposals):
    """Only direct submissions: social previews can be unrelated profile photos."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 32_000_000
    assets = {}
    for proposal in proposals:
        for e in proposal["evidence"]:
            if e.get("image") or e.get("image_omitted_reason") or not e.get("has_original_media"):
                continue
            if e.get("intake_channel") not in ("mobile", "web"):
                continue
            try:
                data = get_bytes(API + "rawimage/by-public-id?" + urllib.parse.urlencode({"public_id": e["id"]}))
            except urllib.error.HTTPError as error:
                if error.code in (404, 410):
                    continue
                raise
            try:
                with Image.open(io.BytesIO(data)) as img:
                    width, height = img.size
                    image_format = img.format
                    if width * height > 32_000_000 or min(width, height) < 250 or image_format not in ("JPEG", "PNG", "WEBP"):
                        continue
                    img.verify()
                # JPEG verify() checks structure but can accept truncated data.
                # Decode fully before publishing the unchanged original bytes.
                with Image.open(io.BytesIO(data)) as img:
                    img.load()
            except (OSError, ValueError, Image.DecompressionBombError):
                continue
            extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[image_format]
            path = f'evidence/{e["id"]}.{extension}'
            e.update(image=path, width=width, height=height)
            assets[path] = data
    return assets


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".gips-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def sync(root=ROOT, rows=None, now=None, download_media=True):
    now = now or datetime.now(timezone.utc)
    proposals = json.loads((root / "proposals.json").read_text())
    state_path = root / "sync-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    rows = fetch_inventory() if rows is None else rows
    if not rows:
        raise ValueError("Empty public inventory")
    proposals, state, stats = ingest_reports(proposals, state, rows, now)
    assets = collect_media(proposals) if download_media else {}
    for p in proposals:
        rank_proposal(p, now)
    state["image_count"] = sum(bool(e.get("image")) for p in proposals for e in p["evidence"])
    state["version"] = hashlib.sha256(json.dumps(proposals, sort_keys=True).encode()).hexdigest()[:16]
    sources_path = root / "evidence/sources.json"
    sources = json.loads(sources_path.read_text()) if sources_path.exists() else []
    sources_by_id = {e["public_id"]: e for e in sources}
    for p in proposals:
        for e in p["evidence"]:
            record = sources_by_id.setdefault(e["id"], {"public_id": e["id"], "source_url": e["url"]})
            if e.get("image") in assets:
                record.update(local_image_path=e["image"], image_source=API + "rawimage/by-public-id?public_id=" + e["id"],
                    has_original_media=True, sha256=hashlib.sha256(assets[e["image"]]).hexdigest())
    # All network work and validation have completed before any durable write.
    for name, data in assets.items():
        atomic_write(root / name, data)
    for path, data in ((root / "proposals.json", proposals), (sources_path, list(sources_by_id.values())), (state_path, state)):
        atomic_write(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode())
    return stats | {"proposals": len(proposals), "images_downloaded": len(assets), "public_inventory": len(rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(sync(args.root)))
