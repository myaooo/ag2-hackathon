"""Tests for backend._profile_block — verifies extract rendering."""

from backend import _profile_block
from schemas import ProfileExtract, UserProfile


def _base_profile(**overrides) -> UserProfile:
    base = dict(
        stage="undergrad",
        field="CS",
        location="NYC",
        risk_tolerance=0.4,
        ambition=0.7,
    )
    base.update(overrides)
    return UserProfile(**base)


def test_profile_block_no_extracts_omits_section():
    block = _profile_block(_base_profile())
    assert "Source extracts" not in block
    # Sanity: the original fields still render
    assert "stage" in block
    assert "field" in block


def test_profile_block_with_extracts_renders_section():
    profile = _base_profile(
        extracts=[
            ProfileExtract(source="github", url="https://github.com/foo", text="GitHub user: foo\nBio: hacker"),
            ProfileExtract(source="linkedin", url="https://linkedin.com/in/foo", text="", fetched=False),
            ProfileExtract(source="paste", text="I want to work in AI"),
        ]
    )
    block = _profile_block(profile)
    assert "(none)\n\nSource extracts:" in block  # blank-line separator pinned
    assert "[github] https://github.com/foo" in block
    assert "GitHub user: foo" in block
    assert "[linkedin] https://linkedin.com/in/foo  (could not fetch — pasted)" in block
    assert "  [paste]\n" in block  # no trailing space, no extra suffix on this line
    assert "I want to work in AI" in block
