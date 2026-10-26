import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DAG = ROOT / "airflow" / "dags" / "fundlake_dag.py"


def test_dag_file_is_valid_python_with_expected_tasks_and_order():
    src = DAG.read_text()
    tree = ast.parse(src)
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert {"validate_contracts", "dbt_silver", "quality_gate", "dbt_gold", "record_audit"} <= funcs
    # results flow task to task, so the order is enforced by data dependencies: contracts -> silver -> gate -> gold -> audit
    for line in ("c = validate_contracts()", "s = dbt_silver(c)", "g = quality_gate(s)", "d = dbt_gold(g)", "record_audit(c, s, g, d)"):
        assert line in src
    assert "catchup=False" in src and "fundlake_daily" in src


@pytest.mark.parametrize("name,expect", [
    ("airflow_dag_test.json", {"validate_contracts": "success", "dbt_silver": "success", "quality_gate": "success", "dbt_gold": "success", "record_audit": "success"}),
    ("airflow_dag_test_gate_fail.json", {"validate_contracts": "success", "dbt_silver": "success", "quality_gate": "failed",
                                         "dbt_gold": "upstream_failed", "record_audit": "upstream_failed"}),
])
def test_recorded_airflow_runs(name, expect):
    """Recorded by scripts/run_airflow_dag.sh (Airflow 3, `airflow dags test`). Re-run the script to refresh them."""
    p = ROOT / "outputs" / name
    if not p.exists():
        pytest.skip("no recorded Airflow run")
    r = json.loads(p.read_text())
    assert {t["task_id"]: t["state"] for t in r["tasks"]} == expect
    assert r["dag_run_state"] == ("success" if "fail" not in name else "failed")
