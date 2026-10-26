import pytest

from fundlake.detection import score
from conftest import RAW

FAULTS = ["duplicate_trade", "missing_price", "stale_price", "stale_fx", "identifier_change", "identifier_conflict", "position_break",
          "claim_before_inception", "orphan_claim", "reported_before_loss", "duplicate_claim",
          "pm_duplicate_notice", "pm_notice_lines_mismatch", "pm_sign_corrected", "pm_unknown_investor", "pm_over_call",
          "pm_missing_nav_statement", "pm_stale_nav", "pm_capital_account_break"]


@pytest.fixture(scope="module")
def s(con):
    return score(con, RAW / "answer_key.json")


@pytest.mark.parametrize("fault", FAULTS)
def test_every_planted_fault_is_found_exactly(s, fault):
    r = s[fault]
    assert r["recall"] == 1.0, r["missed"]
    assert r["precision"] == 1.0, r["false_positive"]


def test_overall(s):
    assert s["_overall"] == dict(planted=156, true_positive=156, false_positive=0)
