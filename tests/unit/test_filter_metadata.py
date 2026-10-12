"""The phase 18a predicates (forwarded, forwarded_from, is_reply, edited, link, filename, forwards,
reactions): model validation, the matcher truth tables, and the flags that build them."""

from datetime import UTC, datetime
from typing import Any

import pytest

from tgmirror.core.gateway import SrcMessage, Unit
from tgmirror.filters.matcher import Matcher
from tgmirror.filters.model import FilterError, FilterSpec
from tgmirror.filters.parser import FlagFilters, from_flags

BASE = datetime(2024, 6, 1, tzinfo=UTC)


def msg(**kw: Any) -> SrcMessage:
    kw.setdefault("id", 1)
    kw.setdefault("date", BASE)
    return SrcMessage(**kw)


def passes(predicates: dict[str, Any], message: SrcMessage) -> bool:
    spec = FilterSpec.from_data({"include": [predicates]})
    return Matcher(spec).matches(Unit((message,)))


# ---- matcher --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wanted", "message", "expected"),
    [
        (True, msg(is_forward=True), True),
        (True, msg(), False),
        (False, msg(), True),
        (False, msg(is_forward=True), False),
    ],
)
def test_forwarded(wanted: bool, message: SrcMessage, expected: bool) -> None:
    assert passes({"forwarded": wanted}, message) is expected


def test_forwarded_from_matches_the_origin_id_and_accepts_a_negative_marked_id() -> None:
    origin = msg(is_forward=True, fwd_from_id=-1001234567890)
    assert passes({"forwarded_from": -1001234567890}, origin)
    assert passes({"forwarded_from": [5, -1001234567890]}, origin)
    assert not passes({"forwarded_from": [5]}, origin)
    # a forward from a user who hides it has a name but no id: never matches an id
    assert not passes({"forwarded_from": [5]}, msg(is_forward=True, fwd_from_name="Hidden"))


@pytest.mark.parametrize(
    ("wanted", "reply", "expected"), [(True, 7, True), (True, None, False), (False, None, True)]
)
def test_is_reply(wanted: bool, reply: int | None, expected: bool) -> None:
    assert passes({"is_reply": wanted}, msg(reply_to_id=reply)) is expected


def test_edited() -> None:
    assert passes({"edited": True}, msg(edit_date=BASE))
    assert not passes({"edited": True}, msg())
    assert passes({"edited": False}, msg())


def test_link_without_domains_means_any_link() -> None:
    assert passes({"link": True}, msg(link_domains=("a.com",)))
    assert not passes({"link": True}, msg())
    assert passes({"link": []}, msg(link_domains=("a.com",)))


def test_link_domain_matches_subdomains_but_not_look_alikes() -> None:
    assert passes({"link": "example.com"}, msg(link_domains=("blog.example.com",)))
    assert passes({"link": ["other.org", "example.com"]}, msg(link_domains=("example.com",)))
    assert not passes({"link": "example.com"}, msg(link_domains=("notexample.com",)))
    assert not passes({"link": "example.com"}, msg())


def test_filename_is_a_case_insensitive_glob_and_false_without_a_file() -> None:
    assert passes({"filename": "*.PDF"}, msg(file_name="Report.pdf"))
    assert passes({"filename": ["a*", "*.zip"]}, msg(file_name="b.ZIP"))
    assert not passes({"filename": "*.pdf"}, msg(file_name="report.docx"))
    assert not passes({"filename": "*"}, msg())


def test_forwards_and_reactions_ranges_are_false_when_unknown() -> None:
    assert passes({"forwards": {"min": 10}}, msg(forwards=10))
    assert not passes({"forwards": {"min": 10}}, msg(forwards=9))
    assert not passes({"forwards": {"max": 10}}, msg())  # unknown fails max too
    assert passes({"reactions": {"min": 1, "max": 5}}, msg(reactions=3))
    assert passes({"reactions": {"max": 0}}, msg(reactions=0))


def test_predicates_of_one_rule_are_anded() -> None:
    both = msg(is_forward=True, link_domains=("a.com",))
    assert passes({"forwarded": True, "link": "a.com"}, both)
    assert not passes({"forwarded": True, "link": "b.com"}, both)


# ---- model ----------------------------------------------------------------------------------


def test_link_is_normalised_and_round_trips_through_the_stored_json() -> None:
    spec = FilterSpec.from_data(
        {"include": [{"link": ["https://www.Example.com/a", "example.com"], "filename": "*.PDF"}]}
    )
    rule = spec.include[0]
    assert rule.link == ("example.com",)
    assert rule.filename == ("*.pdf",)
    assert FilterSpec.from_json(spec.to_json()) == spec


def test_any_link_round_trips_as_an_empty_list() -> None:
    spec = FilterSpec.from_data({"include": [{"link": True}]})
    assert spec.include[0].link == ()
    assert FilterSpec.from_json(spec.to_json()).include[0].link == ()


def test_false_booleans_survive_the_stored_json() -> None:
    spec = FilterSpec.from_data({"include": [{"forwarded": False, "edited": False}]})
    again = FilterSpec.from_json(spec.to_json())
    assert again.include[0].forwarded is False and again.include[0].edited is False


@pytest.mark.parametrize(
    "bad",
    [
        {"link": False},
        {"link": "not a domain"},
        {"forwarded_from": 0},
        {"filename": " "},
        {"forwards": {}},
        {"forwarded": "sometimes"},
    ],
)
def test_bad_values_are_filter_errors(bad: dict[str, Any]) -> None:
    with pytest.raises(FilterError):
        FilterSpec.from_data({"include": [bad]})


# ---- flags ----------------------------------------------------------------------------------


def test_flags_build_the_same_rule_as_yaml() -> None:
    flags = FlagFilters(
        forwarded=True, forwarded_from=[-100, 7], has_link=True, link_domain=["Example.com"]
    )
    rule = from_flags(flags).include[0]
    assert rule.forwarded is True
    assert rule.forwarded_from == (-100, 7)
    assert rule.link == ("example.com",)  # a domain narrows has_link


def test_has_link_alone_is_any_link() -> None:
    assert from_flags(FlagFilters(has_link=True)).include[0].link == ()


def test_not_forwarded_counts_as_a_given_filter() -> None:
    assert FlagFilters(forwarded=False).given
    assert from_flags(FlagFilters(forwarded=False)).include[0].forwarded is False
    assert not FlagFilters().given
    assert not FlagFilters(has_link=False).given
