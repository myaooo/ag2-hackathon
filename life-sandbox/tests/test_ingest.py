"""Pure-helper tests for ingest. Fetchers themselves are smoke-tested manually."""

from ingest import (
    MAX_EXTRACT_CHARS,
    _format_github,
    _is_linkedin_blocked,
    _parse_github_login,
    _strip_html,
    _truncate,
)


def test_truncate_short_string_unchanged():
    assert _truncate("hello") == "hello"


def test_truncate_long_string_clipped():
    s = "x" * (MAX_EXTRACT_CHARS + 100)
    out = _truncate(s)
    assert len(out) == MAX_EXTRACT_CHARS + 1  # +1 for the ellipsis
    assert out.endswith("…")


def test_parse_github_login_basic():
    assert _parse_github_login("https://github.com/torvalds") == "torvalds"


def test_parse_github_login_with_trailing_slash():
    assert _parse_github_login("https://github.com/octocat/") == "octocat"


def test_parse_github_login_ignores_repo_path():
    assert _parse_github_login("https://github.com/torvalds/linux") == "torvalds"


def test_parse_github_login_returns_none_on_non_github():
    assert _parse_github_login("https://example.com/foo") is None


def test_format_github_includes_bio_and_repos():
    user = {
        "login": "octocat",
        "name": "The Octocat",
        "bio": "GitHub mascot",
        "company": "@github",
        "blog": "https://github.blog",
        "followers": 1000,
        "public_repos": 8,
    }
    repos = [
        {"name": "Hello-World", "language": "Ruby", "description": "First repo"},
        {"name": "Spoon-Knife", "language": None, "description": None},
    ]
    out = _format_github(user, repos)
    assert "octocat" in out
    assert "GitHub mascot" in out
    assert "Hello-World (Ruby)" in out
    assert "Spoon-Knife (—)" in out  # None language renders as em-dash


def test_is_linkedin_blocked_on_non_200():
    assert _is_linkedin_blocked(status=403, body="whatever") is True


def test_is_linkedin_blocked_on_authwall_marker():
    assert _is_linkedin_blocked(status=200, body="<html>... authwall ...</html>") is True


def test_is_linkedin_blocked_on_login_title():
    assert _is_linkedin_blocked(status=200, body="<title>LinkedIn Login</title>") is True


def test_is_linkedin_blocked_on_clean_profile():
    body = "<html><h1>Jane Doe</h1><p>Software engineer</p></html>"
    assert _is_linkedin_blocked(status=200, body=body) is False


def test_strip_html_removes_tags_scripts_and_blank_lines():
    html = """
    <html>
      <head><style>body{color:red}</style></head>
      <body>
        <h1>Title</h1>
        <script>alert(1)</script>
        <p>Hello   world</p>
        <p></p>
      </body>
    </html>
    """
    out = _strip_html(html)
    assert "Title" in out
    assert "Hello   world" in out
    assert "alert" not in out
    assert "color:red" not in out
    # No blank lines between content
    for line in out.splitlines():
        assert line.strip() != ""
