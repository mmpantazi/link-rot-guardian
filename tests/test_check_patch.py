import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

from linkrot import cli, patch
from linkrot.check import Checker
from linkrot.extract import extract_text
from tests.helpers import serve


class CheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.base = serve()
        cls.c = Checker(timeout=5, retries=0)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def v(self, path):
        return self.c.check(self.base + path)

    def test_verdicts(self):
        self.assertEqual(self.v("/ok").verdict, "ok")
        self.assertEqual(self.v("/gone").verdict, "dead")
        self.assertEqual(self.v("/410").verdict, "dead")
        self.assertEqual(self.v("/soft").verdict, "soft404")
        self.assertEqual(self.v("/parked").verdict, "soft404")
        self.assertEqual(self.v("/forbidden").verdict, "blocked")
        self.assertEqual(self.v("/temp").verdict, "redirected")

    def test_permanent_redirect(self):
        r = self.v("/old")
        self.assertEqual((r.verdict, r.final_url), ("moved", self.base + "/ok"))

    def test_redirect_to_homepage_is_soft(self):
        self.assertEqual(self.v("/deep/page").verdict, "soft404")

    def test_dead_host(self):
        self.assertEqual(Checker(timeout=3, retries=0).check("http://nonexistent-host-zz.invalid/").verdict, "dead")


class PatchTests(unittest.TestCase):
    def test_apply_multiple_and_html_escape(self):
        t = "[a](http://a.io) [b](http://a.io)"
        ls = extract_text(t, "f.md")
        out = patch.apply(t, [patch.Proposal(l, "https://x/y", "", "m") for l in ls])
        self.assertEqual(out, "[a](https://x/y) [b](https://x/y)")
        h = '<a href="http://a.io">x</a>'
        l = extract_text(h, "f.html", True)
        self.assertEqual(patch.apply(h, [patch.Proposal(l[0], "http://z/?a=1&b=2", "", "m")]),
                         '<a href="http://z/?a=1&amp;b=2">x</a>')


class EndToEnd(unittest.TestCase):
    def test_fix_yes_patches_only_proposals(self):
        srv, base = serve()
        old_env = {}
        try:
            from linkrot import wayback
            wayback.CDX, wayback.AVAIL = base + "/cdx", base + "/avail"
            with tempfile.TemporaryDirectory() as d:
                f = os.path.join(d, "a.md")
                open(f, "w").write(f"[moved]({base}/old) [dead]({base}/gone) [fine]({base}/ok) [nope]({base}/410)\n")
                out = StringIO()
                with redirect_stdout(out):
                    rc = cli.main(["fix", d, "--yes", "--allow-private", "--cache", os.path.join(d, "c.json")])
                text = open(f).read()
                self.assertIn(f"[moved]({base}/ok)", text)
                self.assertIn("/web/20190304000000/" + base + "/gone", text)
                self.assertIn(f"[fine]({base}/ok)", text)
                self.assertIn(f"[nope]({base}/410)", text)
                self.assertIn("patched 2", out.getvalue())
                with redirect_stdout(StringIO()):
                    rc = cli.main(["check", d, "--allow-private", "--no-archive"])
                self.assertEqual(rc, 1)
        finally:
            srv.shutdown()

    def test_dry_run_leaves_file(self):
        srv, base = serve()
        try:
            from linkrot import wayback
            wayback.CDX, wayback.AVAIL = base + "/cdx", base + "/avail"
            with tempfile.TemporaryDirectory() as d:
                f = os.path.join(d, "a.md")
                src = f"[moved]({base}/old)\n"
                open(f, "w").write(src)
                out = StringIO()
                with redirect_stdout(out):
                    cli.main(["fix", d, "--dry-run", "--allow-private", "--no-archive"])
                self.assertEqual(open(f).read(), src)
                self.assertIn("+[moved](" + base + "/ok)", out.getvalue())
        finally:
            srv.shutdown()

    def test_local_links(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "b.md"), "w").write("x")
            open(os.path.join(d, "a.md"), "w").write("[ok](b.md) [bad](missing.md#x) [dir](/)")
            out = StringIO()
            with redirect_stdout(out):
                rc = cli.main(["check", d, "--root", d, "--no-archive"])
            self.assertEqual(rc, 1)
            self.assertIn("missing.md", out.getvalue())
            self.assertNotIn("b.md", out.getvalue())


if __name__ == "__main__":
    unittest.main()
