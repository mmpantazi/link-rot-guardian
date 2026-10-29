import unittest

from linkrot.extract import extract_text


def urls(t, html=False):
    return [l.url for l in extract_text(t, "f.md", html)]


class ExtractTests(unittest.TestCase):
    def test_inline_ref_autolink_bare(self):
        t = "[a](http://a.io/x) and <http://b.io> and http://c.io/p.\n\n[r]: http://d.io/r \"T\"\n"
        self.assertEqual(urls(t), ["http://a.io/x", "http://b.io", "http://c.io/p", "http://d.io/r"])

    def test_wikipedia_parens(self):
        self.assertEqual(urls("[w](https://en.wikipedia.org/wiki/Foo_(bar))"), ["https://en.wikipedia.org/wiki/Foo_(bar)"])
        self.assertEqual(urls("see (https://a.io/x)."), ["https://a.io/x"])

    def test_code_ignored(self):
        t = "```\n[a](http://fenced.io)\n```\n`http://inline.io` [ok](http://real.io)\n"
        self.assertEqual(urls(t), ["http://real.io"])

    def test_no_double_count(self):
        self.assertEqual(len(urls("[http://a.io](http://a.io)")), 2)
        self.assertEqual(len(urls("[x](http://a.io)")), 1)

    def test_html_attr_unescaped_and_span(self):
        t = '<a href="http://a.io/?x=1&amp;y=2">x</a>'
        l = extract_text(t, "f.html", True)[0]
        self.assertEqual(l.url, "http://a.io/?x=1&y=2")
        self.assertEqual(t[l.start:l.end], "http://a.io/?x=1&amp;y=2")

    def test_line_numbers(self):
        self.assertEqual(extract_text("a\n\n[x](http://a.io)", "f.md")[0].line, 3)

    def test_kinds(self):
        ls = extract_text("[a](mailto:x@y.z) [b](#top) [c](docs/a.md) [d](//cdn.io/x.js)", "f.md")
        self.assertEqual([l.kind for l in ls], ["skip", "skip", "local", "skip"])


if __name__ == "__main__":
    unittest.main()
