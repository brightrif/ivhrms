import re
from pathlib import Path

from django.test import SimpleTestCase

from apps.web.templatetags.bs import status_badge

LEGACY_CLASSES = re.compile(
    r'class="(pill|muted|err|ok|big|head|cards|chips|scroll|pager|filters|inline-form|form|card narrow)[ "]')
LEGACY_FRAGMENTS = ['btn small', 'btn ghost', 'btn danger', 'class="btn"', 'class="facts"', "<table>"]


class TemplateStyleTests(SimpleTestCase):
    def test_no_legacy_classes_remain(self):
        folder = Path(__file__).parent / "templates" / "web"
        problems = []
        for path in sorted(folder.glob("*.html")):
            text = path.read_text(encoding="utf-8")
            problems += [f"{path.name}: {m.group(0)}" for m in LEGACY_CLASSES.finditer(text)]
            problems += [f"{path.name}: {frag}" for frag in LEGACY_FRAGMENTS if frag in text]
        self.assertEqual(problems, [], "Templates still use the old compatibility styles")

    def test_status_badge_tones(self):
        self.assertIn("bg-success-subtle", status_badge("approved", "Approved"))
        self.assertIn("bg-danger-subtle", status_badge("rejected", "Rejected"))
        self.assertIn("bg-secondary-subtle", status_badge("something_new", "X"))