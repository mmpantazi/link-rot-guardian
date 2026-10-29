import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

from linkrot import cli, extract, patch
from linkrot.check import quote_url
from tests.helpers import serve


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)

    def w(self, name, text, mode="w", **kw):
        p = os.path.join(self.d.name, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, mode, **kw) as fh:
            fh.write(text)
        return p

    def run_cli(self, *argv):
        out = StringIO()
        with redirect_stdout(out):
            rc = cli.main(list(argv))
        return rc, out.getvalue()


class AnchorTests(Base):
    def test_slug_and_anchor_checks(self):
        self.w("b.md", "# Hello, World!\n## Hello, World!\n```\n# not a heading\n```\n<a id=\"x1\"></a>\n")
        self.w("a.md", "[1](b.md#hello-world) [2](b.md#hello-world-1) [3](b.md#nope) [4](b.md#x1) "
                       "[5](#top-section) [6](#missing)\n\n# Top Section\n")
        rc, out = self.run_cli("check", self.d.name, "--root", self.d.name, "--no-archive")
        self.assertEqual(rc, 1)
        self.assertIn("anchor #nope", out)
        self.assertIn("anchor #missing", out)
        for ok in ("#hello-world ", "#x1", "#top-section"):
            self.assertNotIn("  " + ok, out)
        self.assertEqual(out.count("DEAD"), 2)

    def test_slug(self):
        self.assertEqual(extract.slug("What's `new` in [v2](http://x)?"), "whats-new-in-v2")

    def test_directory_links_resolve_to_index(self):
        self.w("guide/index.html", '<h2 id="intro">x</h2>')
        self.w("a.md", "[g](guide/#intro) [h](guide/#nope)")
        rc, out = self.run_cli("check", self.d.name, "--no-archive")
        self.assertIn("#nope", out)
        self.assertNotIn("#intro", out)


class EncodingTests(Base):
    def test_latin1_file_survives_fix(self):
        srv, base = serve()
        self.addCleanup(srv.shutdown)
        raw = f"caf\xe9 [m]({base}/old)\n".encode("latin-1")
        p = self.w("a.md", raw, "wb")
        rc, out = self.run_cli("fix", self.d.name, "--yes", "--allow-private", "--no-archive")
        self.assertEqual(open(p, "rb").read(), f"caf\xe9 [m]({base}/ok)\n".encode("latin-1"))

    def test_quote_url(self):
        self.assertEqual(quote_url("http://exämple.com/päge a?q=ü#f"),
                         "http://xn--exmple-cua.com/p%C3%A4ge%20a?q=%C3%BC")
        self.assertEqual(quote_url("https://a.io/x%20y?a=1&b=2"), "https://a.io/x%20y?a=1&b=2")


class StaleTests(Base):
    def test_file_changed_since_scan_is_skipped(self):
        srv, base = serve()
        self.addCleanup(srv.shutdown)
        p = self.w("a.md", f"[m]({base}/old)\n")
        links = extract.extract_file(p)
        text = patch.read(p)
        self.assertTrue(patch.is_current(text, links[0]))
        self.assertFalse(patch.is_current("x" + text, links[0]))


class GitFlags(Base):
    def git(self, *a):
        subprocess.run(["git", "-c", "safe.directory=*", "-c", "user.name=t", "-c", "user.email=t@t", *a],
                       cwd=self.d.name, check=True, capture_output=True)

    def test_since_and_fail_on(self):
        srv, base = serve()
        self.addCleanup(srv.shutdown)
        self.git("init", "-q")
        self.w("old.md", f"[d]({base}/gone)\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "one")
        self.w("new.md", f"[m]({base}/old)\n")
        self.git("add", "-A")
        os.chdir(self.d.name)
        self.addCleanup(os.chdir, os.getcwd())
        rc, out = self.run_cli("check", ".", "--since", "HEAD", "--allow-private", "--no-archive")
        self.assertEqual(rc, 0)
        self.assertIn("new.md", out)
        self.assertNotIn("old.md", out)
        rc, _ = self.run_cli("check", ".", "--since", "HEAD", "--allow-private", "--no-archive", "--fail-on", "moved")
        self.assertEqual(rc, 1)
        rc, _ = self.run_cli("check", ".", "--allow-private", "--no-archive", "--fail-on", "never")
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
