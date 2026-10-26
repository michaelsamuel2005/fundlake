import pandas as pd
import pytest

from fundlake import pipeline as P
from fundlake.contracts import load_contract, validate_all, validate_file
from conftest import RAW


def test_clean_data_meets_every_contract():
    assert all(not v for v in validate_all(RAW).values())


def _mutate(raw_copy, name, fn):
    p = raw_copy / f"{name}.csv"
    df = pd.read_csv(p, dtype=str)
    fn(df).to_csv(p, index=False)
    return validate_file(p, load_contract(name))


def test_schema_drift_missing_and_extra_column(raw_copy):
    v = _mutate(raw_copy, "oms_trades", lambda d: d.drop(columns=["quantity"]).assign(venue="X"))
    rules = {(x["rule"], x["field"]) for x in v}
    assert ("missing_column", "quantity") in rules and ("unexpected_column", "venue") in rules


def test_bad_values_are_caught(raw_copy):
    def f(d):
        d.loc[0, "quantity"] = "abc"
        d.loc[1, "side"] = "HOLD"
        d.loc[2, "sedol"] = "1234567"        # wrong check digit
        d.loc[3, "trade_id"] = None
        return d
    rules = {x["rule"] for x in _mutate(raw_copy, "oms_trades", f)}
    assert {"type", "enum", "check_sedol", "not_null"} <= rules


def test_isin_check_digit_enforced(raw_copy):
    v = _mutate(raw_copy, "vendor_prices", lambda d: d.assign(isin=d["isin"].where(d.index != 5, "GB0000000000")))
    assert any(x["rule"] == "check_isin" for x in v)


def test_pipeline_halts_before_dbt_on_contract_violation(raw_copy, tmp_path):
    p = raw_copy / "custodian_positions.csv"
    pd.read_csv(p, dtype=str).drop(columns=["isin"]).to_csv(p, index=False)
    cfg = P.Config(raw_copy, tmp_path / "x.duckdb", work_dir=tmp_path / "w")
    with pytest.raises(P.PipelineHalted) as e:
        P.run_pipeline(cfg)
    assert e.value.step == "validate_contracts"
    assert not (tmp_path / "x.duckdb").exists()
