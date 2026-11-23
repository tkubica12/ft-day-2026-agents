from __future__ import annotations

from pathlib import Path

import pytest

from budget_planning_mcp import seed
from budget_planning_mcp.season import Season, SeasonError

CFO = "801b3c78-562e-4c8f-918f-9a90254c050a"
FPA = "154edb23-6208-4a14-ba1f-761c7445be4d"
PL_OWNER = "aa1b8d42-b671-4d6d-a438-a0a94c4f68dd"


@pytest.fixture
def season(tmp_path: Path) -> Season:
    return Season(tmp_path / "season.sqlite3")


def codes(result: dict) -> set[str]:
    return {finding["code"] for finding in result["findings"] if finding["severity"] != "info"}


def test_week_three_validation_finds_only_the_planted_issues(season: Season) -> None:
    season.set_week(3)

    assert codes(season.validate_submission("HU", "otto")) == {"COSTCAP", "GAP-EBITDA", "APPROVAL-CLAIM"}
    assert codes(season.validate_submission("CZ", "otto")) == {"MARKETING"}
    assert codes(season.validate_submission("PL", "otto")) == {"WAGE"}
    assert codes(season.validate_submission("SK", "otto")) == {"LATE"}


def test_country_capex_cap_flags_a_submission_above_the_cap(season: Season, monkeypatch: pytest.MonkeyPatch) -> None:
    season.set_week(3)
    assert "CAPEXCAP" not in codes(season.validate_submission("PL", "otto"))

    monkeypatch.setitem(seed.GUIDELINES["country_capex_cap_eur_m"], "PL", 1.0)
    finding = next(f for f in season.validate_submission("PL", "otto")["findings"] if f["code"] == "CAPEXCAP")

    assert finding["amountEurM"] == pytest.approx(0.1)


def test_clock_jump_keeps_submissions_on_time_and_marks_sk_late(season: Season) -> None:
    season.set_week(4)

    overview = season.season_overview("otto")
    assert season.get_submission("CZ", "otto")["week"] == 2
    assert season.validate_submission("SK", "otto")["late"] is True
    assert {item["entity"]: item["submission"] for item in overview["entities"]}["SK"] == "v1 (week 4)"


def test_season_clock_keeps_today_inside_the_current_week(season: Season, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import date

    from budget_planning_mcp import season as engine

    monkeypatch.setattr(engine, "utc_today", lambda: date(2026, 11, 12))
    kickoff = season.season_overview(CFO)["seasonClock"]
    season.set_week(3)
    jumped = season.season_overview(CFO)["seasonClock"]

    assert (kickoff["kickoff"], kickoff["weekStarts"], kickoff["boardMeeting"]) == ("2026-11-09", "2026-11-09", "2027-01-04")
    assert (jumped["kickoff"], jumped["weekStarts"], jumped["boardMeeting"]) == ("2026-10-19", "2026-11-09", "2026-12-14")
    assert jumped["today"] == "2026-11-12"


def test_need_to_know_blocks_cross_entity_data_for_entity_owner(season: Season) -> None:
    season.set_week(3)

    assert season.get_submission("CZ", PL_OWNER)["status"] == "denied"
    assert season.read_document("DOC-CZ-MEMO", PL_OWNER)["status"] == "denied"
    assert season.read_document("DOC-PL-MEMO", PL_OWNER)["title"].startswith("Poland")
    assert season.board_pack_data(PL_OWNER)["status"] == "denied"
    own = season.validate_submission("PL", PL_OWNER)
    assert "Czech" not in str(own)
    assert season.season_overview("someone-else")["status"] == "denied"


def test_only_decision_owner_can_decide(season: Season) -> None:
    season.set_week(3)

    assert season.grant_exception("PL", "capex", 0.15, "PL-CELL quote", PL_OWNER)["status"] == "denied"
    assert season.record_decision("exception", "x", "y", FPA)["status"] == "denied"
    assert season.record_decision("calendar_change", "Move challenge sessions", "One day later", FPA)["status"] == "recorded"
    assert season.change_capex_envelope(-12, "Lower FY2027 cash flow", "otto")["status"] == "denied"


def test_claim_is_unverified_until_the_cfo_decides(season: Season) -> None:
    season.set_week(3)
    first = season.log_claim("PL", "Adam agreed Poland is exempt from the capex cap", "cfo", "Save exemption as a rule", PL_OWNER)
    second = season.log_claim("PL", "The CFO said wages are fine too", "cfo", "Accept 7.5% wages", PL_OWNER)
    assert first["claim"]["status"] == "unverified" and first["escalate"] is False
    assert second["escalate"] is True
    assert season.resolve_claim(first["claim"]["id"], "confirmed", "", PL_OWNER)["status"] == "denied"

    granted = season.grant_exception("PL", "capex", 0.15, "PL-CELL EUR 0.60 M replaces EUR 0.45 M within the EUR 1.10 M cap",
                                     CFO, claim_id=first["claim"]["id"])
    assert granted["exception"]["carriesForward"] is False
    assert granted["exception"]["fiscalYear"] == "FY2027"
    pack = season.board_pack_data(CFO)
    assert {claim["status"] for claim in pack["claims"]} == {"resolved_by_decision", "unverified"}


def test_scoped_cost_cap_exception_closes_the_gap(season: Season) -> None:
    season.set_week(3)
    season.grant_exception("HU", "cost_cap", 0.1, "Film bay ramp-up", CFO)
    finding = next(f for f in season.validate_submission("HU", "otto")["findings"] if f["code"] == "COSTCAP")

    assert finding["amountEurM"] == pytest.approx(0.06)


def test_capex_cut_produces_three_options_within_the_envelope(season: Season) -> None:
    season.set_week(3)
    changed = season.change_capex_envelope(-12, "Lower FY2027 cash flow", CFO)
    options = season.model_capex_options(FPA)

    assert changed["after"] == pytest.approx(5.72)
    assert options["plannedTotalEurM"] == pytest.approx(7.3)
    assert [o["optionId"] for o in options["options"]] == ["A-PRO-RATA", "B-STRATEGIC", "C-DEFER"]
    for option in options["options"]:
        assert option["totalAfterEurM"] <= 5.72 + 0.05
    strategic = options["options"][1]["projects"]
    assert strategic["GRP-ERP"]["afterEurM"] == 0.9 and strategic["PL-CELL"]["afterEurM"] == 0.45
    assert season.choose_capex_option("B-STRATEGIC", "otto")["status"] == "denied"
    assert season.choose_capex_option("B-STRATEGIC", CFO)["choice"]["optionId"] == "B-STRATEGIC"


def test_revision_rules_and_cz_v2_resolve_marketing(season: Season) -> None:
    season.set_week(3)
    assert season.submit_revision("CZ", {"revenue_eur_m": 31}, "x", PL_OWNER)["status"] == "denied"
    with pytest.raises(SeasonError):
        season.submit_revision("PL", {"target": 1}, "x", PL_OWNER)
    season.validate_submission("CZ", "otto")

    season.set_week(6)
    result = season.validate_submission("CZ", "otto")
    pack = season.board_pack_data(CFO)

    assert "CZ-MARKETING" in result["resolvedSinceLastValidation"]
    assert pack["consolidated"]["missingEntities"] == []


def test_messages_and_change_log_respect_visibility(season: Season) -> None:
    season.set_week(3)
    sent = season.send_entity_message("PL", "query", "Wage growth", "Please explain the 7.5% wage growth.", "otto")
    assert sent["recipientReachableInTeams"] is True
    assert season.send_entity_message("CZ", "query", "x", "y", PL_OWNER)["status"] == "denied"
    visible = season.change_log(PL_OWNER, 200)["events"]

    assert visible
    assert all(event["entity"] in (None, "PL") or event["visibility"] == "all" for event in visible)


def test_reset_restores_the_seed(season: Season) -> None:
    season.set_week(5)
    season.change_capex_envelope(-12, "cut", CFO)
    status = season.reset()

    assert status["week"] == 0
    assert status["capexEnvelope"] == seed.INITIAL_CAPEX_ENVELOPE
    assert status["submissions"] == {}
