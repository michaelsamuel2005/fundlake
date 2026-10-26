import json

import duckdb
import pandas as pd
import pytest

from fundlake import pipeline as P
from conftest import RAW


def test_layers_exist_and_counts(con):
    n = lambda t: con.execute(f"select count(*) from {t}").fetchone()[0]
    assert n("bronze.bronze_oms_trades") == 769 and n("silver.silver_trades") == 737 and n("gold.fact_trade") == 737
    assert n("gold.fact_nav") == 256 and n("gold.recon_position_breaks") == 71


def test_dbt_tests_pass_with_expected_warnings(built):
    info = built["info"]
    assert info["dbt_silver"]["summary"].get("error", 0) == 0 and info["dbt_silver"]["summary"].get("fail", 0) == 0
    g = info["dbt_gold"]["summary"]
    assert g.get("error", 0) == 0 and g.get("fail", 0) == 0
    assert g.get("warn", 0) >= 3  # fact_price carried/stale, fact_position breaks, fact_nav breaches: the planted faults, surfaced as warnings


def test_gate_passes_clean_data_and_reports_every_check(built):
    checks = built["info"]["quality_gate"]["checks"]
    assert len(checks) == 10 and all(c["passed"] for c in checks)


def test_gate_blocks_gold_when_identifiers_cannot_be_resolved(raw_copy, tmp_path):
    p = raw_copy / "secmaster_identifiers.csv"
    d = pd.read_csv(p, dtype=str)
    isins = d[d.id_type == "ISIN"].security_id.unique()[:25]       # lose 25 of 60 ISIN mappings
    d[~((d.id_type == "ISIN") & d.security_id.isin(isins))].to_csv(p, index=False)
    cfg = P.Config(raw_copy, tmp_path / "g.duckdb", work_dir=tmp_path / "w")
    with pytest.raises(P.PipelineHalted) as e:
        P.run_pipeline(cfg)
    assert e.value.step == "quality_gate"
    failed = {c["check"] for c in e.value.detail}
    assert {"unresolved_position_security_rate", "unresolved_price_security_rate"} <= failed
    con = duckdb.connect(str(cfg.db_path), read_only=True)
    schemas = {r[0] for r in con.execute("select schema_name from information_schema.schemata").fetchall()}
    assert "silver" in schemas and "gold" not in schemas      # gold never built


def test_audit_trail_records_inputs_code_and_outputs(built, con):
    rec = json.loads(con.execute("select record from audit.build_runs").fetchone()[0])
    assert set(rec["input_sha256"]) >= {"oms_trades.csv", "vendor_prices.csv", "custodian_positions.csv"}
    assert len(rec["gold_sha256"]) == 64 and len(rec["dbt_code_sha256"]) == 64 and rec["row_counts"]["gold.fact_nav"] == 256
    assert rec["dbt_version"] and rec["duckdb_version"] and rec["fundlake_version"] == "0.1.0"


def test_build_is_reproducible(built, tmp_path):
    cfg2 = P.Config(RAW, tmp_path / "again.duckdb", work_dir=tmp_path / "w", now_utc="2026-10-27T00:00:00+00:00")
    info2 = P.run_pipeline(cfg2)
    a, b = built["info"]["audit"], info2["audit"]
    assert a["gold_sha256"] == b["gold_sha256"] and a["build_id"] == b["build_id"]


def test_changed_input_changes_build_id_and_gold_hash(built, raw_copy, tmp_path):
    p = raw_copy / "oms_counterparties.csv"
    p.write_text(p.read_text().replace("Broker A", "Broker Alpha"))
    info2 = P.run_pipeline(P.Config(raw_copy, tmp_path / "chg.duckdb", work_dir=tmp_path / "w"))
    assert info2["audit"]["build_id"] != built["info"]["audit"]["build_id"]
    assert info2["audit"]["gold_sha256"] != built["info"]["audit"]["gold_sha256"]


def test_pipeline_step_order_and_short_circuit(monkeypatch, tmp_path):
    calls = []
    def mk(name, fail=False):
        def f(cfg):
            calls.append(name)
            if fail:
                raise P.PipelineHalted(name, "boom")
            return {}
        return f
    monkeypatch.setattr(P, "step_validate_contracts", mk("contracts"))
    monkeypatch.setattr(P, "step_dbt_silver", mk("silver"))
    monkeypatch.setattr(P, "step_quality_gate", mk("gate", fail=True))
    monkeypatch.setattr(P, "step_dbt_gold", mk("gold"))
    with pytest.raises(P.PipelineHalted):
        P.run_pipeline(P.Config(RAW, tmp_path / "z.duckdb"))
    assert calls == ["contracts", "silver", "gate"]       # gold never runs after a gate failure
