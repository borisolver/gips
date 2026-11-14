"""Check crawler metadata and the actual deploy artifact, without live ingestion."""

import importlib.util
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
URL = "https://borisolver.github.io/gips/"
IMAGE_URL = URL + "share/gips-v1.png"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Head(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.canonical = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name")
            if key:
                if key in self.meta:
                    raise AssertionError(f"Duplicate metadata: {key}")
                self.meta[key] = attrs.get("content")
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonical = attrs.get("href")


class SharePreviewTests(unittest.TestCase):
    def test_offline_build_emits_complete_absolute_share_metadata(self):
        builder = load("gips_share_build", ROOT / "build.py")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            builder.build(output)
            head = Head()
            head.feed((output / "index.html").read_text())
        self.assertEqual(head.canonical, URL)
        self.assertEqual(head.meta["og:url"], URL)
        self.assertEqual(head.meta["og:type"], "website")
        self.assertEqual(head.meta["og:image"], IMAGE_URL)
        self.assertEqual(head.meta["twitter:image"], IMAGE_URL)
        self.assertEqual(head.meta["twitter:card"], "summary_large_image")
        self.assertEqual(head.meta["og:image:type"], "image/png")
        self.assertEqual((head.meta["og:image:width"], head.meta["og:image:height"]), ("1200", "630"))
        self.assertEqual(head.meta["twitter:image:alt"], head.meta["og:image:alt"])
        self.assertIn("Independent project", head.meta["og:image:alt"])
        self.assertNotIn("{{", head.meta["twitter:description"])

    def test_public_artifact_includes_exact_png_without_design_sources(self):
        updater = load("gips_share_artifact", ROOT / "scripts/run_update.py")
        with tempfile.TemporaryDirectory() as temp:
            artifact = Path(temp) / "public"
            updater.prepare_artifact(artifact)
            self.assertEqual((artifact / "share/gips-v1.png").read_bytes(), (ROOT / "share/gips-v1.png").read_bytes())
            self.assertFalse((artifact / "design").exists())
            self.assertFalse((artifact / "scripts").exists())
            with Image.open(artifact / "share/gips-v1.png") as image:
                image.load()
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (1200, 630))
                self.assertEqual(image.mode, "RGB")

if __name__ == "__main__":
    unittest.main()
