# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import logging
import socket
import urllib.request
from dataclasses import FrozenInstanceError

import pytest

from url_import import (
    MAX_IMPORT_CANDIDATES,
    MAX_IMPORT_TEXT_BYTES,
    UrlImportBatchError,
    UrlImportStatus,
    UrlInvalidReason,
    plan_url_import,
)


pytestmark = pytest.mark.unit


def test_bom_crlf_blanks_and_source_line_numbers() -> None:
    text = "\ufeff  example.test/a  \r\n\r\n\thttps://example.test/b\t\r\n"

    plan = plan_url_import(text)

    assert plan.is_valid_batch
    assert [item.source_line for item in plan.candidates] == [1, 3]
    assert [item.normalized_url for item in plan.candidates] == [
        "https://example.test/a",
        "https://example.test/b",
    ]
    assert plan.counts.total == plan.source_candidate_count == 2


def test_lf_only_parser_does_not_treat_other_control_characters_as_lines() -> None:
    plan = plan_url_import("example.test/a\vexample.test/b\u2028example.test/c")

    assert len(plan.candidates) == 1
    assert plan.candidates[0].status is UrlImportStatus.INVALID
    assert plan.candidates[0].invalid_reason is UrlInvalidReason.CONTROL_CHARACTER


@pytest.mark.parametrize(
    "source_text",
    [
        "https://example.test/a https://example.test/b",
        "[label](https://example.test)",
        '<a href="https://example.test">',
        "https://example.test,Label",
        "# comment",
    ],
)
def test_other_text_formats_are_not_interpreted(source_text: str) -> None:
    plan = plan_url_import(source_text)

    assert len(plan.candidates) == 1
    assert plan.candidates[0].status is UrlImportStatus.INVALID


def test_stored_url_preserves_spelling_after_outer_trim() -> None:
    source = "HTTPS://Example.TEST:443/A%2fb//?b=2&a=&a=1+2#Frag%2f"

    candidate = plan_url_import(f"  {source}  ").candidates[0]

    assert candidate.status is UrlImportStatus.READY
    assert candidate.normalized_url == source


def test_bare_host_port_gets_https_scheme() -> None:
    candidate = plan_url_import("example.test:8443/path?x=1#frag").candidates[0]

    assert candidate.normalized_url == "https://example.test:8443/path?x=1#frag"
    assert candidate.fallback_name == "path"


@pytest.mark.parametrize(
    ("source_text", "reason"),
    [
        ("ftp://example.test/file", UrlInvalidReason.UNSUPPORTED_SCHEME),
        ("gopher://example.test/file", UrlInvalidReason.UNSUPPORTED_SCHEME),
        ("com.example://host.test/path", UrlInvalidReason.UNSUPPORTED_SCHEME),
        ("mailto:user@example.test", UrlInvalidReason.UNSUPPORTED_SCHEME),
        ("tel:911", UrlInvalidReason.UNSUPPORTED_SCHEME),
        ("urn:123", UrlInvalidReason.UNSUPPORTED_SCHEME),
        ("ssh:22", UrlInvalidReason.UNSUPPORTED_SCHEME),
        ("https:///path", UrlInvalidReason.MISSING_HOST),
        ("https://user:pass@example.test/", UrlInvalidReason.CREDENTIALS),
        ("https://@example.test/", UrlInvalidReason.CREDENTIALS),
        ("https://exa mple.test/", UrlInvalidReason.EMBEDDED_WHITESPACE),
        ("https://exa\tmple.test/", UrlInvalidReason.CONTROL_CHARACTER),
        ("https://example.test/\x00", UrlInvalidReason.CONTROL_CHARACTER),
        ("https://example_test/", UrlInvalidReason.INVALID_HOST),
        ("https://a..example.test/", UrlInvalidReason.INVALID_HOST),
        ("https://-bad.example.test/", UrlInvalidReason.INVALID_HOST),
        ("https://bad-.example.test/", UrlInvalidReason.INVALID_HOST),
        ("https://999.1.1.1/", UrlInvalidReason.INVALID_HOST),
        ("https://2130706433/", UrlInvalidReason.INVALID_HOST),
        ("https://0x7f000001/", UrlInvalidReason.INVALID_HOST),
        ("https://0x7f.0x0.0x0.0x1/", UrlInvalidReason.INVALID_HOST),
        ("https://1.2.3.0x4/", UrlInvalidReason.INVALID_HOST),
        ("https://²¹³⁰⁷⁰⁶⁴³³/", UrlInvalidReason.INVALID_HOST),
        ("https://０ｘ７ｆ.０.０.１/", UrlInvalidReason.INVALID_HOST),
        ("https://０x７f.０x０.０x０.０x１/", UrlInvalidReason.INVALID_HOST),
        ("https://example.123/", UrlInvalidReason.INVALID_HOST),
        ("https://example.0x1/", UrlInvalidReason.INVALID_HOST),
        ("https://example.0x/", UrlInvalidReason.INVALID_HOST),
        ("https://a.1.2.3/", UrlInvalidReason.INVALID_HOST),
        ("https://example.①/", UrlInvalidReason.INVALID_HOST),
        ("https://\u0301a.example.test/", UrlInvalidReason.INVALID_HOST),
        ("https://😀.example.test/", UrlInvalidReason.INVALID_HOST),
        ("https://xn--e28h.example.test/", UrlInvalidReason.INVALID_HOST),
        ("https://example\u3002test/", UrlInvalidReason.INVALID_HOST),
        ("https://[v1.example]/", UrlInvalidReason.INVALID_HOST),
        ("https://[fe80::1%25eth0]/", UrlInvalidReason.INVALID_HOST),
        ("https://[bad/", UrlInvalidReason.MALFORMED_URL),
        ("https://example.test:/", UrlInvalidReason.INVALID_PORT),
        ("https://example.test:0/", UrlInvalidReason.INVALID_PORT),
        ("https://example.test:abc/", UrlInvalidReason.INVALID_PORT),
        ("https://example.test:+443/", UrlInvalidReason.INVALID_PORT),
        ("https://example.test:٤٤٣/", UrlInvalidReason.INVALID_PORT),
        ("https://example.test:65536/", UrlInvalidReason.INVALID_PORT),
        ("https://example.test/%", UrlInvalidReason.MALFORMED_URL),
        ("https://example.test/%0", UrlInvalidReason.MALFORMED_URL),
        ("https://example.test/%ZZ", UrlInvalidReason.MALFORMED_URL),
        ("https://example.test/path\\next", UrlInvalidReason.MALFORMED_URL),
        ("https:example.test", UrlInvalidReason.MISSING_HOST),
    ],
)
def test_invalid_rows_have_categorical_reasons(
    source_text: str, reason: UrlInvalidReason
) -> None:
    candidate = plan_url_import(source_text).candidates[0]

    assert candidate.status is UrlImportStatus.INVALID
    assert candidate.invalid_reason is reason
    assert candidate.normalized_url is None
    assert candidate.fallback_name is None


def test_overlong_numeric_port_is_invalid_instead_of_raising() -> None:
    candidate = plan_url_import("https://example.test:" + "9" * 5000).candidates[0]

    assert candidate.status is UrlImportStatus.INVALID
    assert candidate.invalid_reason is UrlInvalidReason.INVALID_PORT


@pytest.mark.parametrize(
    "source_text",
    [
        "https://bücher.example.test/",
        "http://192.0.2.1/path",
        "https://[2001:db8::1]/",
        "https://localhost:8443/",
    ],
)
def test_valid_hostname_forms_are_accepted_without_network(source_text: str) -> None:
    assert plan_url_import(source_text).candidates[0].status is UrlImportStatus.READY


@pytest.mark.parametrize(
    ("source_text", "expected_name"),
    [
        ("example.test/reports/Q2%20Plan/", "Q2 Plan"),
        ("example.test/caf%C3%A9", "café"),
        ("example.test/A+B", "A+B"),
        ("example.test/%2520", "%20"),
        ("example.test/reports/%20/", "reports"),
        ("example.test/reports/%00/", "reports"),
        ("example.test/reports/%2F/", "reports"),
        ("example.test/reports/%EF%B8%8F/", "reports"),
        ("example.test/reports/../", "reports"),
        ("example.test/?name=ignored#also-ignored", "example.test"),
        ("example.test:8443/", "example.test"),
    ],
)
def test_offline_fallback_names(source_text: str, expected_name: str) -> None:
    candidate = plan_url_import(source_text).candidates[0]

    assert candidate.status is UrlImportStatus.READY
    assert candidate.fallback_name == expected_name


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("HTTP://Example.TEST", "http://example.test:80/"),
        ("HTTPS://Example.TEST", "https://example.test:443/"),
        ("example.test", "https://EXAMPLE.TEST/"),
    ],
)
def test_authorized_identity_normalizations_match(first: str, second: str) -> None:
    plan = plan_url_import(f"{first}\n{second}")

    assert [item.status for item in plan.candidates] == [
        UrlImportStatus.READY,
        UrlImportStatus.DUPLICATE_IN_BATCH,
    ]
    assert plan.candidates[1].duplicate_of_line == 1


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("http://example.test/", "https://example.test/"),
        ("https://example.test:444/", "https://example.test/"),
        ("https://example.test/A", "https://example.test/a"),
        ("https://example.test/docs", "https://example.test/docs/"),
        ("https://example.test/a//b", "https://example.test/a/b"),
        ("https://example.test/%7E", "https://example.test/~"),
        ("https://example.test/%7e", "https://example.test/%7E"),
        ("https://example.test/?a=1&b=2", "https://example.test/?b=2&a=1"),
        ("https://example.test/?a=1", "https://example.test/?a=2"),
        ("https://example.test/#A", "https://example.test/#a"),
        ("https://www.example.test/", "https://example.test/"),
        ("https://example.test/a/../b", "https://example.test/b"),
        ("https://example.test./", "https://example.test/"),
        ("https://bücher.example.test/", "https://xn--bcher-kva.example.test/"),
        ("https://example.test/path", "https://example.test/path?"),
        ("https://example.test/path", "https://example.test/path#"),
    ],
)
def test_conservative_identity_does_not_collapse_distinct_urls(
    first: str, second: str
) -> None:
    plan = plan_url_import(f"{first}\n{second}")

    assert [item.status for item in plan.candidates] == [
        UrlImportStatus.READY,
        UrlImportStatus.READY,
    ]


def test_distinct_urls_may_share_the_same_fallback_name() -> None:
    plan = plan_url_import("example.test/a/report\nexample.test/b/report")

    assert [item.status for item in plan.candidates] == [
        UrlImportStatus.READY,
        UrlImportStatus.READY,
    ]
    assert [item.fallback_name for item in plan.candidates] == ["report", "report"]


def test_invalid_rows_do_not_establish_batch_identity() -> None:
    plan = plan_url_import("ftp://example.test/file\nftp://example.test/file")

    assert [item.status for item in plan.candidates] == [
        UrlImportStatus.INVALID,
        UrlImportStatus.INVALID,
    ]
    assert all(item.duplicate_of_line is None for item in plan.candidates)


def test_batch_duplicate_references_original_line_across_blanks() -> None:
    plan = plan_url_import("example.test/path\n\n\nhttps://EXAMPLE.TEST:443/path")

    assert [item.source_line for item in plan.candidates] == [1, 4]
    assert plan.candidates[1].status is UrlImportStatus.DUPLICATE_IN_BATCH
    assert plan.candidates[1].duplicate_of_line == 1


def test_existing_url_identity_normalizes_default_port_and_root() -> None:
    plan = plan_url_import(
        "HTTP://Example.TEST\nHTTPS://Example.TEST",
        target_tab_urls=["http://example.test:80/"],
        other_tab_urls=["https://example.test:443/"],
    )

    assert [item.status for item in plan.candidates] == [
        UrlImportStatus.DUPLICATE_IN_TARGET_TAB,
        UrlImportStatus.DUPLICATE_ON_OTHER_TAB,
    ]


def test_duplicate_precedence_ordering_and_counts() -> None:
    text = "\n".join(
        [
            "https://example.test/unique",
            "HTTPS://EXAMPLE.TEST:443/target",
            "https://example.test/target",
            "https://example.test/other",
            "https://EXAMPLE.TEST:443/other",
            "https://example.test/both",
            "ftp://example.test/invalid",
        ]
    )

    plan = plan_url_import(
        text,
        target_tab_urls=[
            "https://example.test/target",
            "https://example.test/both",
        ],
        other_tab_urls=[
            "https://example.test/other",
            "https://example.test/both",
        ],
    )

    assert [item.status for item in plan.candidates] == [
        UrlImportStatus.READY,
        UrlImportStatus.DUPLICATE_IN_TARGET_TAB,
        UrlImportStatus.DUPLICATE_IN_BATCH,
        UrlImportStatus.DUPLICATE_ON_OTHER_TAB,
        UrlImportStatus.DUPLICATE_IN_BATCH,
        UrlImportStatus.DUPLICATE_IN_TARGET_TAB,
        UrlImportStatus.INVALID,
    ]
    assert plan.candidates[2].duplicate_of_line == 2
    assert plan.candidates[4].duplicate_of_line == 4
    assert plan.counts.total == 7
    assert plan.counts.ready == 1
    assert plan.counts.invalid == 1
    assert plan.counts.duplicate_in_batch == 2
    assert plan.counts.duplicate_in_target_tab == 2
    assert plan.counts.duplicate_on_other_tab == 1


def test_invalid_existing_urls_are_ignored_and_inputs_are_not_mutated() -> None:
    target_urls = ["not a URL"]
    other_urls = ["ftp://example.test/file"]

    plan = plan_url_import(
        "https://example.test/ready",
        target_tab_urls=target_urls,
        other_tab_urls=other_urls,
    )

    assert plan.candidates[0].status is UrlImportStatus.READY
    assert target_urls == ["not a URL"]
    assert other_urls == ["ftp://example.test/file"]


def test_exact_candidate_limit_is_accepted() -> None:
    text = "\n\n".join(
        f"https://example.test/{index}" for index in range(MAX_IMPORT_CANDIDATES)
    )

    plan = plan_url_import(text)

    assert plan.is_valid_batch
    assert plan.source_candidate_count == MAX_IMPORT_CANDIDATES
    assert plan.counts.ready == MAX_IMPORT_CANDIDATES


def test_candidate_limit_plus_one_rejects_the_whole_batch() -> None:
    text = "\n".join("ftp://example.test/file" for _ in range(501))

    plan = plan_url_import(text)

    assert plan.batch_error is UrlImportBatchError.TOO_MANY_CANDIDATES
    assert plan.source_candidate_count == MAX_IMPORT_CANDIDATES + 1
    assert plan.candidates == ()
    assert plan.counts.total == 0


def test_exact_utf8_byte_limit_is_accepted() -> None:
    prefix = "example.test\n"
    text = prefix + " " * (MAX_IMPORT_TEXT_BYTES - len(prefix.encode("utf-8")))

    plan = plan_url_import(text)

    assert plan.is_valid_batch
    assert len(text.encode("utf-8")) == MAX_IMPORT_TEXT_BYTES
    assert plan.source_candidate_count == 1


def test_utf8_byte_limit_plus_one_rejects_the_whole_batch() -> None:
    prefix = "example.test\n"
    text = prefix + " " * (MAX_IMPORT_TEXT_BYTES + 1 - len(prefix.encode("utf-8")))

    plan = plan_url_import(text)

    assert plan.batch_error is UrlImportBatchError.TEXT_TOO_LARGE
    assert plan.candidates == ()
    assert plan.counts.total == 0


def test_text_limit_is_measured_in_utf8_bytes() -> None:
    text = "é" * (MAX_IMPORT_TEXT_BYTES // 2 + 1)

    assert len(text) < MAX_IMPORT_TEXT_BYTES
    assert plan_url_import(text).batch_error is UrlImportBatchError.TEXT_TOO_LARGE


def test_planning_is_offline_and_does_not_log_sensitive_urls(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fail_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "getaddrinfo", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    sensitive = "https://example.test/path?token=do-not-log#private"

    with caplog.at_level(logging.DEBUG):
        plan = plan_url_import(sensitive)

    assert plan.candidates[0].status is UrlImportStatus.READY
    assert sensitive not in caplog.text
    assert "do-not-log" not in repr(plan)


def test_plan_and_candidates_are_immutable() -> None:
    plan = plan_url_import("example.test")

    with pytest.raises(FrozenInstanceError):
        plan.source_candidate_count = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        plan.candidates[0].source_line = 2  # type: ignore[misc]
