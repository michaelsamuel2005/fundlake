"""Airflow DAG: validate contracts -> dbt bronze/silver -> QUALITY GATE -> dbt gold -> audit record.

Each task calls the same step function the local runner uses (fundlake.pipeline), so the gate logic is unit tested without
Airflow, and this DAG is run end to end with `airflow dags test` by scripts/run_airflow_dag.sh (Airflow in its own virtual
environment, dbt in another, as is usual to avoid dependency clashes). A gate failure raises PipelineHalted: the gate task
fails and dbt_gold and record_audit never run, so gold is left as it was. Results pass between tasks through XCom, so the
audit record carries the test and gate results of the run that produced it."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

try:                                    # Airflow 3
    from airflow.sdk import dag, task
except ImportError:                     # Airflow 2
    from airflow.decorators import dag, task

from fundlake import pipeline as P

ROOT = Path(__file__).resolve().parents[2]
RAW = Path(os.environ.get("FUNDLAKE_RAW", ROOT / "data" / "raw"))
DB = Path(os.environ.get("FUNDLAKE_DB", ROOT / "data" / "fundlake.duckdb"))


def _cfg() -> P.Config:
    return P.Config(RAW, DB, orchestrator="airflow")


@dag(dag_id="fundlake_daily", schedule="0 7 * * 1-5", start_date=datetime(2026, 7, 1), catchup=False,
     default_args={"retries": 0}, tags=["fundlake", "synthetic"])
def fundlake_daily():
    @task
    def validate_contracts() -> dict:
        return P.step_validate_contracts(_cfg())

    @task
    def dbt_silver(contracts: dict) -> dict:
        return P.step_dbt_silver(_cfg())

    @task
    def quality_gate(silver: dict) -> dict:
        return P.step_quality_gate(_cfg())        # raises PipelineHalted on a breach: downstream tasks do not run

    @task
    def dbt_gold(gate: dict) -> dict:
        return P.step_dbt_gold(_cfg())

    @task
    def record_audit(contracts: dict, silver: dict, gate: dict, gold: dict) -> dict:
        rec = P.step_record_audit(_cfg(), {"validate_contracts": contracts, "dbt_silver": silver, "quality_gate": gate, "dbt_gold": gold})
        return {"build_id": rec["build_id"], "gold_sha256": rec["gold_sha256"], "dbt_tests": rec["dbt_tests"]}

    c = validate_contracts()
    s = dbt_silver(c)
    g = quality_gate(s)
    d = dbt_gold(g)
    record_audit(c, s, g, d)


fundlake_daily()
