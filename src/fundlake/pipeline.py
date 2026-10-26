"""Pipeline: contracts -> bronze+silver (dbt) -> quality gate -> gold (dbt) -> audit record.
The same step functions are used by the local runner and by the Airflow DAG, so the gate logic is tested without Airflow."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import yaml

from . import __version__
from .contracts import CONTRACT_DIR, contract_hash, validate_all

ROOT = Path(__file__).resolve().parents[2]
DBT_DIR = ROOT / "dbt"
DBT = os.environ.get("FUNDLAKE_DBT", "dbt")   # dbt can live in its own environment (as with Airflow, to avoid dependency clashes)
GOLD_TABLES = ["dim_date", "dim_fund", "dim_counterparty", "dim_security", "dim_policy", "fact_fx", "fact_price", "fact_trade",
               "fact_position", "fact_nav_component", "fact_nav", "fact_claim", "recon_position_breaks", "mart_fund_performance",
               "mart_claims_summary", "mart_loss_ratio", "lineage_position", "client_policy_view", "si_asset_listing",
               "dim_pm_fund", "fact_pm_cashflow", "fact_pm_nav", "pm_recon_capital_account", "pm_nav_asof", "pm_irr",
               "mart_pm_performance", "mart_pm_by_strategy"]


class PipelineHalted(RuntimeError):
    def __init__(self, step: str, detail):
        super().__init__(f"{step} failed: {detail}")
        self.step, self.detail = step, detail


@dataclass
class Config:
    raw_dir: Path
    db_path: Path
    work_dir: Path = field(default_factory=lambda: Path(tempfile.mkdtemp(prefix="fundlake_"))
                           )
    target: str = "duckdb"
    now_utc: str | None = None  # injectable for reproducible audit records
    orchestrator: str = "local runner"


def _dbt(cfg: Config, select: str) -> dict:
    env = dict(os.environ, FUNDLAKE_DB=str(cfg.db_path), FUNDLAKE_TARGET=cfg.target, DBT_SEND_ANONYMOUS_USAGE_STATS="false")
    tgt = cfg.work_dir / "target"
    cmd = [DBT, "build", "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR), "--select", select,
           "--target-path", str(tgt), "--log-path", str(cfg.work_dir / "logs"), "--target", cfg.target,
           "--vars", json.dumps({"raw_path": str(Path(cfg.raw_dir).resolve())}), "--no-use-colors", "--quiet", "--indirect-selection", "buildable"]
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=DBT_DIR)
    rr = json.loads((tgt / "run_results.json").read_text()) if (tgt / "run_results.json").exists() else {"results": []}
    res = [dict(unique_id=r["unique_id"], status=r["status"], failures=r.get("failures")) for r in rr["results"]]
    summary: dict = {}
    for r in res:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    return dict(returncode=p.returncode, summary=summary, results=res, stderr=p.stdout[-2000:] if p.returncode else "")


def step_validate_contracts(cfg: Config) -> dict:
    v = validate_all(cfg.raw_dir)
    bad = {k: x for k, x in v.items() if x}
    if bad:
        raise PipelineHalted("validate_contracts", bad)
    return dict(sources_checked=len(v), violations=0)


def step_dbt_silver(cfg: Config) -> dict:
    r = _dbt(cfg, "tag:bronze tag:silver")
    errors = [x for x in r["results"] if x["status"] in ("error", "fail")]
    if r["returncode"] != 0 or errors:
        raise PipelineHalted("dbt_silver", dict(errors=errors[:5], stderr=r["stderr"]))
    return dict(summary=r["summary"])


def _scalar(con, sql):
    return con.execute(sql).fetchone()[0]


# Portable SQL (runs on DuckDB and Snowflake): one query per gate threshold in contracts/gate.yaml.
GATE_QUERIES = {
    "unresolved_trade_security_rate": "select avg(case when security_id is null then 1.0 else 0 end) from silver.silver_trades",
    "unresolved_position_security_rate": "select avg(case when security_id is null then 1.0 else 0 end) from silver.silver_custodian_positions",
    "unresolved_price_security_rate": "select avg(case when security_id is null then 1.0 else 0 end) from silver.silver_prices",
    "missing_price_rate": ("select coalesce((select count(*) from silver.dq_findings where finding_type='missing_price'),0)*1.0 / "
                           "nullif((select count(*) from (select distinct security_id, position_date from silver.silver_custodian_positions where quantity>0) x),0)"),
    "duplicate_trade_rate": "select 1.0 - (select count(*) from silver.silver_trades) * 1.0 / (select count(*) from bronze.bronze_oms_trades)",
    "invalid_claim_rate": "select avg(case when invalid_reason is not null then 1.0 else 0 end) from silver.silver_claims",
    "null_quantity_rate": ("select (select count(*) from silver.silver_trades where quantity is null) + "
                           "(select count(*) from silver.silver_custodian_positions where quantity is null)"),
    "min_rows_trades": "select count(*) from silver.silver_trades",
    "min_rows_positions": "select count(*) from silver.silver_custodian_positions",
    "pm_notice_issue_rate": ("select (select count(*) from silver.dq_findings where finding_type in "
                             "('pm_duplicate_notice', 'pm_notice_lines_mismatch', 'pm_sign_corrected', 'pm_unknown_investor')) * 1.0 "
                             "/ (select count(*) from silver.silver_pm_notices)"),
}


def run_gate_checks(con, gate: dict) -> list[dict]:
    """Evaluate every threshold against the silver layer. `con` is a DuckDB connection or any DB-API cursor. One result per check."""
    q = GATE_QUERIES
    out = []
    for name, spec in gate["thresholds"].items():
        val = _scalar(con, q[name])
        val = float(val) if val is not None else float("nan")
        ok = (val <= spec["max"]) if "max" in spec else (val >= spec["min"])
        out.append(dict(check=name, value=val, limit=spec.get("max", spec.get("min")), kind="max" if "max" in spec else "min",
                        passed=bool(ok), description=spec["description"]))
    return out


def step_quality_gate(cfg: Config) -> dict:
    gate = yaml.safe_load((CONTRACT_DIR / "gate.yaml").read_text())
    con = duckdb.connect(str(cfg.db_path))
    try:
        res = run_gate_checks(con, gate)
    finally:
        con.close()
    failed = [r for r in res if not r["passed"]]
    if failed:
        raise PipelineHalted("quality_gate", failed)
    return dict(checks=res)


def step_dbt_gold(cfg: Config) -> dict:
    r = _dbt(cfg, "tag:gold")
    errors = [x for x in r["results"] if x["status"] in ("error", "fail")]
    if r["returncode"] != 0 or errors:
        raise PipelineHalted("dbt_gold", dict(errors=errors[:5], stderr=r["stderr"]))
    return dict(summary=r["summary"])


def canonical_hash(con: duckdb.DuckDBPyConnection, tables: list[str], schema: str = "gold") -> str:
    """Order- and float-noise-insensitive hash of the gold layer."""
    h = hashlib.sha256()
    for t in sorted(tables):
        df = con.execute(f"select * from {schema}.{t}").df()
        for c in df.select_dtypes("float").columns:
            df[c] = df[c].round(6) + 0.0          # + 0.0 turns -0.0 into 0.0
        df = df.astype(str).sort_values(list(df.columns)).reset_index(drop=True)
        h.update(t.encode())
        h.update(df.to_csv(index=False).encode())
    return h.hexdigest()


def _dbt_version() -> str:
    try:
        import dbt.version
        return dbt.version.__version__
    except ImportError:                      # dbt installed in a separate environment: ask the executable
        import re
        out = subprocess.run([DBT, "--version"], capture_output=True, text=True).stdout
        m = re.search(r"installed:\s*([\d.]+)", out)
        return m.group(1) if m else "unknown"


def step_record_audit(cfg: Config, step_info: dict) -> dict:
    """Audit trail: what ran, on which inputs, with which code, and what it produced."""
    raw = Path(cfg.raw_dir)
    inputs = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(raw.glob("*.csv"))}
    code = hashlib.sha256()
    for p in sorted(list((DBT_DIR / "models").rglob("*.sql")) + list((DBT_DIR / "macros").rglob("*.sql")) + list((DBT_DIR / "tests").rglob("*.sql"))):
        code.update(p.read_bytes())
    con = duckdb.connect(str(cfg.db_path))
    try:
        counts = {}
        for sch in ("bronze", "silver", "gold"):
            for (t,) in con.execute("select table_name from information_schema.tables where table_schema=?", [sch]).fetchall():
                counts[f"{sch}.{t}"] = _scalar(con, f"select count(*) from {sch}.{t}")
        gold_hash = canonical_hash(con, GOLD_TABLES)
        build_id = hashlib.sha256(json.dumps([inputs, code.hexdigest(), contract_hash(), __version__], sort_keys=True).encode()).hexdigest()[:16]
        rec = dict(build_id=build_id, built_at_utc=cfg.now_utc or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   fundlake_version=__version__, dbt_version=_dbt_version(), orchestrator=cfg.orchestrator, duckdb_version=duckdb.__version__, target=cfg.target,
                   input_sha256=inputs, dbt_code_sha256=code.hexdigest(), contracts_sha256=contract_hash(), row_counts=counts,
                   dbt_tests={k: v.get("summary") for k, v in step_info.items() if isinstance(v, dict) and "summary" in v},
                   gate=step_info.get("quality_gate", {}).get("checks"), gold_sha256=gold_hash)
        con.execute("create schema if not exists audit")
        con.execute("create table if not exists audit.build_runs (build_id varchar, built_at_utc varchar, record json)")
        con.execute("insert into audit.build_runs values (?, ?, ?)", [build_id, rec["built_at_utc"], json.dumps(rec, default=str)])
    finally:
        con.close()
    return rec


def run_pipeline(cfg: Config) -> dict:
    info: dict = {}
    for name, fn in (("validate_contracts", step_validate_contracts), ("dbt_silver", step_dbt_silver),
                     ("quality_gate", step_quality_gate), ("dbt_gold", step_dbt_gold)):
        info[name] = fn(cfg)
    info["audit"] = step_record_audit(cfg, info)
    return info


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Run the fundlake pipeline")
    ap.add_argument("--raw", default=str(ROOT / "data/raw"))
    ap.add_argument("--db", default=str(ROOT / "data/fundlake.duckdb"))
    ap.add_argument("--target", default="duckdb")
    a = ap.parse_args(argv)
    try:
        info = run_pipeline(Config(Path(a.raw), Path(a.db), target=a.target))
    except PipelineHalted as e:
        print(f"HALTED at {e.step}: {json.dumps(e.detail, default=str)[:1500]}", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in info["audit"].items() if k in ("build_id", "gold_sha256", "row_counts")} | {"gate_passed": all(c["passed"] for c in info["quality_gate"]["checks"])}, indent=1)[:1800])
    return 0


if __name__ == "__main__":
    sys.exit(main())
