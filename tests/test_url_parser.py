"""Stage 1 tests. Run with:  python -m unittest discover -s tests -v"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analyzer.errors import InvalidURLError
from analyzer.url_parser import parse_repo_url


class ValidURLs(unittest.TestCase):
    def check(self, url, owner="psf", repo="requests"):
        ref = parse_repo_url(url)
        self.assertEqual((ref.owner, ref.repo), (owner, repo), msg=url)

    def test_standard_forms(self):
        for url in [
            "https://github.com/psf/requests",
            "http://github.com/psf/requests",
            "https://www.github.com/psf/requests",
            "github.com/psf/requests",
            "https://github.com/psf/requests/",
            "https://github.com/psf/requests.git",
            "https://github.com/psf/requests/tree/main/src",
            "https://github.com/psf/requests/blob/main/README.md",
            "https://github.com/psf/requests?tab=readme",
            "https://github.com/psf/requests#readme",
            "git@github.com:psf/requests.git",
            "psf/requests",
            "  https://github.com/psf/requests  ",
            "HTTPS://GITHUB.COM/psf/requests",
        ]:
            self.check(url)

    def test_names_with_dots_hyphens_underscores(self):
        self.check("https://github.com/vercel/next.js", "vercel", "next.js")
        self.check("https://github.com/my-org/my_repo-2", "my-org", "my_repo-2")


class InvalidURLs(unittest.TestCase):
    def test_rejected(self):
        for url in [
            "",
            "   ",
            "hello",
            "https://gitlab.com/psf/requests",
            "https://github.com/psf",
            "https://github.com/",
            "ftp://github.com/psf/requests",
            "https://github.com/explore/topics",
            "https://github.com/-bad-/repo",
            "https://github.com/psf/..",
            "https://github.com/psf/re po",
            "https://notgithub.com/psf/requests",
            "not a url",
            "https://github.com.evil.com/psf/requests",
        ]:
            with self.assertRaises(InvalidURLError, msg=repr(url)):
                parse_repo_url(url)


if __name__ == "__main__":
    unittest.main()
