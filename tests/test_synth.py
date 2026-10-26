import hashlib

from fundlake.synth import _luhn_isin, cusip_check, cusip_valid, generate, isin_valid, sedol_check, sedol_valid
from conftest import RAW


def test_known_check_digits():
    # public worked examples of the three schemes
    assert isin_valid("US0378331005") and not isin_valid("US0378331006")
    assert sedol_valid("0263494") and not sedol_valid("0263495")
    assert cusip_valid("037833100") and not cusip_valid("037833101")
    assert sedol_check("026349") == "0263494" and cusip_check("03783310") == "037833100"
    assert _luhn_isin("US037833100") == "US0378331005"


def test_generator_is_deterministic(tmp_path):
    generate(tmp_path)
    for p in sorted(RAW.glob("*")):
        assert hashlib.sha256(p.read_bytes()).hexdigest() == hashlib.sha256((tmp_path / p.name).read_bytes()).hexdigest(), p.name


def test_answer_key_counts(key):
    assert key["counts"] == {"identifier_change": 1, "identifier_conflict": 1, "position_break": 5, "missing_price": 8, "stale_price": 1,
                             "stale_fx": 1, "duplicate_trade": 32, "claim_before_inception": 5, "orphan_claim": 4,
                             "reported_before_loss": 2, "duplicate_claim": 3,
                             "pm_capital_account_break": 4, "pm_missing_nav_statement": 1, "pm_stale_nav": 3, "pm_over_call": 1,
                             "pm_unknown_investor": 1, "pm_notice_lines_mismatch": 2, "pm_sign_corrected": 2, "pm_duplicate_notice": 3}
    assert "SYNTHETIC" in key["label"]
