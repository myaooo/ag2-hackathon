"""Backwards-compat + new-shape tests for UserProfile and ingest schemas."""

from schemas import (
    IngestRequest,
    IngestResponse,
    IngestSummary,
    ProfileExtract,
    UserProfile,
)


def test_user_profile_default_extracts_empty():
    profile = UserProfile(
        stage="undergrad",
        field="CS",
        location="NYC",
        risk_tolerance=0.4,
        ambition=0.7,
    )
    assert profile.extracts == []


def test_user_profile_accepts_extracts():
    profile = UserProfile(
        stage="undergrad",
        field="CS",
        location="NYC",
        risk_tolerance=0.4,
        ambition=0.7,
        extracts=[
            ProfileExtract(source="github", url="https://github.com/x", text="bio"),
            ProfileExtract(source="paste", text="hand-typed"),
        ],
    )
    assert len(profile.extracts) == 2
    assert profile.extracts[0].source == "github"
    assert profile.extracts[1].url is None


def test_ingest_request_all_optional():
    req = IngestRequest()
    assert req.linkedin_url is None
    assert req.github_url is None
    assert req.other_url is None
    assert req.pasted_text is None


def test_ingest_summary_shape():
    summary = IngestSummary(field="CS", stage="undergrad", notes_seed="hello")
    assert summary.stage == "undergrad"


def test_ingest_response_shape():
    resp = IngestResponse(
        summary=IngestSummary(field="CS", stage="undergrad", notes_seed="x"),
        extracts=[ProfileExtract(source="github", url="u", text="t")],
    )
    assert resp.summary.field == "CS"
    assert len(resp.extracts) == 1
