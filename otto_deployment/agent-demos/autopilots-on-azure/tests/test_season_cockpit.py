import unittest

from scripts import season_cockpit


def snapshot(week=3, claim="plain claim"):
    validation = {
        "CZ": {"name": "Czech retail", "late": False, "findings": []},
        "RO": {"name": "Romanian retail", "late": True,
               "findings": [{"severity": "high", "title": "Cost cap breached", "amountEurM": 2.4}]},
        "SK": {"name": "Slovak retail", "late": False, "findings": []},
    }
    submissions = {
        "CZ": [{"version": 1, "week": 2, "submittedBy": "Petr"}],
        "RO": [{"version": 1, "week": 3, "submittedBy": "Tomas"}],
    }
    return {
        "observedUtc": "2026-11-12T22:00:00+00:00",
        "export": {
            "group": "Javorna Group", "fiscalYear": "FY2027",
            "state": {
                "week": week, "outbox": [{"entity": "SK", "kind": "brief", "to": "Zuzana", "toTitle": "MD"}],
                "submissions": submissions, "decisions": [], "exceptions": [],
                "claims": [{"week": 3, "claimant": "Emma", "claim": claim, "status": "unverified"}],
            },
            "validation": validation,
            "boardPack": {
                "consolidated": {"entities": [], "revenueEurM": 1.0, "ebitdaEurM": 0.1},
                "capex": {"envelopeHistory": [{"value": 120.0}], "envelopeEurM": 120.0, "plannedTotalEurM": 100.0},
                "openFindings": {"RO": validation["RO"]["findings"]},
            },
            "events": [{"week": 3, "kind": "claim_logged", "summary": "Claim", "actor": "Otto"}],
        },
        "pendingLearning": [], "approvedLearnings": [], "jobs": [],
    }


class SeasonCockpitTests(unittest.TestCase):
    def test_entity_status_reflects_submissions_findings_and_the_season_clock(self):
        rows = {row["code"]: row for row in season_cockpit.entity_rows(snapshot()["export"])}

        self.assertEqual(("Clean", "Petr"), (rows["CZ"]["status"], rows["CZ"]["owner"]))
        self.assertEqual("Needs action", rows["RO"]["status"])
        self.assertEqual(("Overdue", "Zuzana"), (rows["SK"]["status"], rows["SK"]["owner"]))
        self.assertEqual("Waiting", season_cockpit.entity_rows(snapshot(week=2)["export"])[2]["status"])

    def test_render_escapes_participant_text(self):
        page = season_cockpit.render(snapshot(claim="<script>alert(1)</script>"))

        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn('http-equiv="refresh"', page)
        self.assertIn('http-equiv="refresh" content="30"', season_cockpit.render(snapshot(), 30))

    def test_approved_learnings_are_read_only_from_their_playbook_section(self):
        playbook = "## Rules\n- not a learning\n\n## Approved learnings\n\n- Ask RO for wage bridge\n- Check capex\n\n## Next\n- no"

        self.assertEqual(["Ask RO for wage bridge", "Check capex"], season_cockpit.approved_learnings(playbook))
        self.assertEqual([], season_cockpit.approved_learnings("## Approved learnings\n\nNone yet.\n"))


if __name__ == "__main__":
    unittest.main()
