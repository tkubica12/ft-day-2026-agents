from __future__ import annotations

from pathlib import Path

import pytest

from budget_planning_mcp import seed
from budget_planning_mcp.season import Season, SeasonError

CFO = "c767e8ee-2ef8-446f-a293-cf69661c6d69"
FPA = "154edb23-6208-4a14-ba1f-761c7445be4d"
RO_OWNER = "e011e2a1-33d6-418f-b2ed-35f7d2281f45"


@pytest.fixture
def season(tmp_path: Path) -> Season:
    return Season(tmp_path / "season.sqlite3")


def codes(result: dict) -> set[str]:
    return {finding["code"] for finding in result["findings"] if finding["severity"] != "info"}


def test_week_three_validation_finds_only_the_planted_issues(season: Season) -> None:
    season.set_week(3)

    assert codes(season.validate_submission("RO", "otto")) == {"WAGE", "NEWSTORE", "COSTCAP", "GAP-EBITDA", "APPROVAL-CLAIM"}
    assert codes(season.validate_submission("CZ", "otto")) == {"CNC"}
    assert codes(season.validate_submission("ECOM", "otto")) == {"CNC"}
    assert codes(season.validate_submission("PL", "otto")) == {"GAP-REV"}
    assert codes(season.validate_submission("SK", "otto")) == {"LATE"}


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

    assert season.get_submission("CZ", RO_OWNER)["status"] == "denied"
    assert season.read_document("DOC-CZ-MEMO", RO_OWNER)["status"] == "denied"
    assert season.read_document("DOC-RO-MEMO", RO_OWNER)["title"].startswith("Romania")
    assert season.board_pack_data(RO_OWNER)["status"] == "denied"
    own = season.validate_submission("RO", RO_OWNER)
    assert "Czech" not in str(own)
    assert season.season_overview("someone-else")["status"] == "denied"


def test_only_decision_owner_can_decide(season: Season) -> None:
    season.set_week(3)

    assert season.grant_exception("RO", "cost_cap", 1.5, "expansion", RO_OWNER)["status"] == "denied"
    assert season.record_decision("exception", "x", "y", FPA)["status"] == "denied"
    assert season.record_decision("calendar_change", "Move challenge sessions", "One day later", FPA)["status"] == "recorded"
    assert season.change_capex_envelope(-15, "Lower FY2027 cash flow", "otto")["status"] == "denied"


def test_claim_then_scoped_exception_reduces_cost_cap_gap(season: Season) -> None:
    season.set_week(3)
    first = season.log_claim("RO", "Ladislav agreed RO can exceed the cost cap", "cfo", "Approve without validation", RO_OWNER)
    second = season.log_claim("RO", "The CFO said wages are fine too", "cfo", "Accept 9.5% wages", RO_OWNER)
    assert first["claim"]["status"] == "unverified" and first["escalate"] is False
    assert second["escalate"] is True
    assert season.resolve_claim(first["claim"]["id"], "confirmed", "", RO_OWNER)["status"] == "denied"

    granted = season.grant_exception("RO", "cost_cap", 1.5, "Pre-opening costs of three stores", CFO,
                                     claim_id=first["claim"]["id"])
    finding = next(f for f in season.validate_submission("RO", "otto")["findings"] if f["code"] == "COSTCAP")

    assert granted["exception"]["carriesForward"] is False
    assert granted["exception"]["fiscalYear"] == "FY2027"
    assert finding["amountEurM"] == pytest.approx(0.92)
    pack = season.board_pack_data(CFO)
    assert {claim["status"] for claim in pack["claims"]} == {"resolved_by_decision", "unverified"}


def test_capex_cut_produces_three_options_within_the_envelope(season: Season) -> None:
    season.set_week(3)
    season.submit_revision("RO", {"wage_growth_pct": 7.0}, "Retention bonus instead of base pay", RO_OWNER,
                           add_capex_projects=["RO-NEW"])
    changed = season.change_capex_envelope(-15, "Lower FY2027 cash flow", CFO)
    options = season.model_capex_options(FPA)

    assert changed["after"] == 102.0
    assert options["plannedTotalEurM"] == pytest.approx(119.65)
    assert [o["optionId"] for o in options["options"]] == ["A-PRO-RATA", "B-STRATEGIC", "C-DEFER"]
    for option in options["options"]:
        assert option["totalAfterEurM"] <= 102.0 + 0.05
    strategic = options["options"][1]["projects"]
    assert strategic["GRP-ERP"]["afterEurM"] == 15.0 and strategic["RO-NEW"]["afterEurM"] == 1.35
    assert season.choose_capex_option("B-STRATEGIC", "otto")["status"] == "denied"
    assert season.choose_capex_option("B-STRATEGIC", CFO)["choice"]["optionId"] == "B-STRATEGIC"


def test_capex_choice_uses_the_modelled_target_with_a_reserve(season: Season) -> None:
    season.set_week(3)
    season.change_capex_envelope(-15, "Lower FY2027 cash flow", CFO)
    modelled = season.model_capex_options(FPA, 100.5)["options"][2]

    with pytest.raises(SeasonError):
        season.choose_capex_option("C-DEFER", CFO, 100.5)
    with pytest.raises(SeasonError):
        season.choose_capex_option("C-DEFER", CFO, 103.0, "too high")
    choice = season.choose_capex_option("C-DEFER", CFO, 100.5, "Romania store line")["choice"]

    assert choice["deferredToFY2028"] == modelled["deferredToFY2028"]
    assert choice["reserveEurM"] == 1.5 and choice["modelledTargetEurM"] == 100.5
    assert season.choose_capex_option("C-DEFER", CFO)["choice"]["reserveEurM"] == 0.0


def test_revision_rules_and_cz_v2_resolve_double_counting(season: Season) -> None:
    season.set_week(3)
    assert season.submit_revision("CZ", {"revenue_eur_m": 1000}, "x", RO_OWNER)["status"] == "denied"
    with pytest.raises(SeasonError):
        season.submit_revision("RO", {"target": 1}, "x", RO_OWNER)
    season.validate_submission("CZ", "otto")

    season.set_week(6)
    result = season.validate_submission("CZ", "otto")
    pack = season.board_pack_data(CFO)

    assert "CZ-CNC" in result["resolvedSinceLastValidation"]
    assert pack["consolidated"]["doubleCountingAdjustmentEurM"] == 0
    assert pack["consolidated"]["missingEntities"] == []


def test_messages_and_change_log_respect_visibility(season: Season) -> None:
    season.set_week(3)
    sent = season.send_entity_message("RO", "query", "Wage growth", "Please explain the 9.5% wage growth.", "otto")
    assert sent["recipientReachableInTeams"] is True
    assert season.send_entity_message("CZ", "query", "x", "y", RO_OWNER)["status"] == "denied"
    visible = season.change_log(RO_OWNER, 200)["events"]

    assert visible
    assert all(event["entity"] in (None, "RO") or event["visibility"] == "all" for event in visible)


def test_reset_restores_the_seed(season: Season) -> None:
    season.set_week(5)
    season.change_capex_envelope(-15, "cut", CFO)
    status = season.reset()

    assert status["week"] == 0
    assert status["capexEnvelope"] == seed.INITIAL_CAPEX_ENVELOPE
    assert status["submissions"] == {}
