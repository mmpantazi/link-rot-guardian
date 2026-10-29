import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

from linkrot import cli, wayback
from linkrot.check import Checker
from tests.helpers import serve


class MoreTests(unittest.TestCase):
    def setUp(self):
        self.srv, self.base = serve()
        self.addCleanup(self.srv.shutdown)
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.f = os.path.join(self.d.name, "a.md")

    def run_cli(self, *argv):
        out = StringIO()
        with redirect_stdout(out):
            rc = cli.main(list(argv))
        return rc, out.getvalue()

    def test_interactive_prompt_answers(self):
        b = self.base
        open(self.f, "w").write(f"[a]({b}/old#sec)\n[b]({b}/old?x=1)\n[c]({b}/old/)\n")
        answers = iter(["y", "n", "q"])
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch("builtins.input", lambda _: next(answers)):
            rc, out = self.run_cli("fix", self.d.name, "--allow-private", "--no-archive", "--no-soft")
        text = open(self.f).read().splitlines()
        self.assertEqual(text[0], f"[a]({b}/ok#sec)")
        self.assertEqual(text[1], f"[b]({b}/old?x=1)")
        self.assertIn("patched 1", out)

    def test_ignore_file_and_default_ignores(self):
        b = self.base
        open(self.f, "w").write(f"[a]({b}/gone) [b](http://example.com/x) [c](http://localhost:1/x)\n")
        ign = os.path.join(self.d.name, "ig")
        open(ign, "w").write("# skip\n/gone$\n")
        rc, out = self.run_cli("check", self.d.name, "--allow-private", "--no-archive", "--ignore-file", ign)
        self.assertEqual(rc, 1)  # localhost:1 is dead; /gone and example.com are ignored
        self.assertNotIn("/gone", out)
        self.assertNotIn("example.com", out)

    def test_json_output(self):
        open(self.f, "w").write(f"[a]({self.base}/gone)\n")
        rc, out = self.run_cli("check", self.d.name, "--allow-private", "--no-archive", "--format", "json")
        import json
        row = json.loads(out)[0]
        self.assertEqual((row["verdict"], row["line"]), ("dead", 1))

    def test_soft404_title_must_be_short(self):
        body = ("<title>" + "Why 404 pages matter and how to design them well for your users " * 2 + "</title>").encode()
        from linkrot.check import _soft
        self.assertEqual(_soft(body, {"Content-Type": "text/html"}, "u"), "")

    def test_closest_snapshot_picked_client_side(self):
        rows = [["h"], ["k", "20120530003402"], ["k", "20120602000000"], ["k", "20120525050747"]]
        with mock.patch.object(wayback, "_get_json", return_value=rows):
            w = wayback.Wayback(None, min_interval=0)
            self.assertEqual(w.closest("http://x", "20120601").timestamp, "20120602000000")


if __name__ == "__main__":
    unittest.main()
