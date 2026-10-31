from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agentgate.subject import (
    Subject,
    compute_subject,
    parse_changed_paths,
    subject_mismatch,
)

SUBJECT = Subject(
    base_commit="2468771aec02148b9fe5de59003edc80147d1d7c",
    head_sha="e66415b161daad59861917300824a47acc9e081a",
    diff_sha256="9f8e7d6c5b4a39281706f5e4d3c2b1a09f8e7d6c5b4a39281706f5e4d3c2b1a0",
    changed_files=("hello.txt", "tests/test_sample.py"),
)


def test_parse_changed_paths_sorts_and_reads_renames() -> None:
    status = (
        " M hello.txt\n"
        "A  tests/test_added.py\n"
        "R  old_name.py -> new_name.py\n"
        "?? scratch/\n"
    )

    assert parse_changed_paths(status) == (
        "hello.txt",
        "new_name.py",
        "scratch/",
        "tests/test_added.py",
    )


def test_parse_changed_paths_undoes_git_quoting() -> None:
    status = 'A  "path with \\"quote\\".txt"\n'

    assert parse_changed_paths(status) == ('path with "quote".txt',)


def test_compute_subject_hashes_the_frozen_patch(tmp_path: Path) -> None:
    patch = tmp_path / "diff.patch"
    patch.write_text("diff --git a/x b/x\n", encoding="utf-8")

    subject = compute_subject(
        base_commit=None,
        head_sha="e66415b161daad59861917300824a47acc9e081a",
        patch_path=patch,
        status_text="A  x\n",
    )

    assert subject.diff_sha256 == hashlib.sha256(patch.read_bytes()).hexdigest()
    assert subject.base_commit == ""
    assert subject.changed_files == ("x",)


def test_echo_that_matches_produces_no_complaint() -> None:
    assert subject_mismatch(SUBJECT, SUBJECT.as_document()) == ""


def test_echo_that_reorders_files_still_matches() -> None:
    document = SUBJECT.as_document()
    document["changed_files"] = list(reversed(document["changed_files"]))

    assert subject_mismatch(SUBJECT, document) == ""


def test_truncated_hashes_are_a_transcription_slip_not_a_different_subject() -> None:
    document = SUBJECT.as_document()
    document["head_sha"] = SUBJECT.head_sha[:7]
    document["diff_sha256"] = SUBJECT.diff_sha256[:12]

    assert subject_mismatch(SUBJECT, document) == ""


def test_missing_subject_names_the_field() -> None:
    assert subject_mismatch(SUBJECT, None) == "verdict.subject is missing"


def test_different_head_is_reported_with_both_values() -> None:
    document = SUBJECT.as_document()
    document["head_sha"] = "0" * 40

    problem = subject_mismatch(SUBJECT, document)

    assert "verdict.subject.head_sha" in problem
    assert SUBJECT.head_sha in problem
    assert "0" * 40 in problem


def test_extra_changed_file_is_reported() -> None:
    document = SUBJECT.as_document()
    document["changed_files"] = ["hello.txt"]

    problem = subject_mismatch(SUBJECT, document)

    assert "changed_files" in problem
    assert "tests/test_sample.py" in problem


def test_missing_changed_files_is_reported() -> None:
    document = SUBJECT.as_document()
    del document["changed_files"]

    assert "changed_files: missing" in subject_mismatch(SUBJECT, document)


def test_digest_tracks_every_field() -> None:
    other = Subject(
        base_commit=SUBJECT.base_commit,
        head_sha=SUBJECT.head_sha,
        diff_sha256=SUBJECT.diff_sha256,
        changed_files=("hello.txt",),
    )

    assert SUBJECT.digest() != other.digest()


def test_report_states_which_evidence_the_verdict_is_bound_to() -> None:
    from agentgate.report import _subject_fact

    rounds = [
        {"round": 1, "verdict": {"verdict": "PASS", "subject": SUBJECT.as_document()}}
    ]

    fact = _subject_fact(rounds)

    assert fact is not None
    assert "base 2468771" in fact
    assert "head e66415b" in fact
    assert "diff 9f8e7d6c" in fact
    assert "2 个文件" in fact


def test_report_omits_the_line_when_there_is_no_subject() -> None:
    from agentgate.report import _subject_fact

    assert _subject_fact([{"round": 1, "verdict": {"verdict": "PASS"}}]) is None
    assert _subject_fact([]) is None


def test_subject_document_is_json_serialisable() -> None:
    assert json.loads(json.dumps(SUBJECT.as_document()))["head_sha"] == SUBJECT.head_sha
