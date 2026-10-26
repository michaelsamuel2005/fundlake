"""Offline checks of scripts/run_snowflake.py: the load statements and gate queries parse as Snowflake SQL, and the
table comparison it uses to prove parity with DuckDB finds real differences and ignores float noise. The runner itself
needs a Snowflake account; its results are recorded in outputs/snowflake_run.json when it has been run."""
import importlib.util
from pathlib import Path

import pytest

sqlglot = pytest.importorskip("sqlglot")
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_snowflake", ROOT / "scripts" / "run_snowflake.py")
RS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RS)


def test_load_plan_covers_every_source_and_parses():
    plan = RS.load_plan(ROOT / "data" / "raw")
    assert len(plan) == 17
    for s in RS.LOAD_PREAMBLE:
        sqlglot.parse_one(s, read="snowflake")
    for p in plan:
        for s in p["sql"]:
            sqlglot.parse_one(s, read="snowflake")


def test_gate_queries_parse_as_snowflake_sql():
    from fundlake.pipeline import GATE_QUERIES
    for q in GATE_QUERIES.values():
        sqlglot.parse_one(q, read="snowflake")


def test_compare_tables_ignores_float_noise_and_catches_real_differences(con):
    cols, rows = RS.table_rows(con, "select * from gold.mart_pm_performance")
    same = RS.compare_tables(cols, rows, cols, list(rows))
    assert same["identical_within_tolerance"]
    i = cols.index("tvpi")
    noisy = [tuple((v * (1 + 1e-12) if j == i else v) for j, v in enumerate(r)) for r in rows]
    assert RS.compare_tables(cols, noisy, cols, rows)["identical_within_tolerance"]
    wrong = [tuple((v + 0.01 if j == i and k == 0 else v) for j, v in enumerate(r)) for k, r in enumerate(rows)]
    assert not RS.compare_tables(cols, wrong, cols, rows)["identical_within_tolerance"]
    assert not RS.compare_tables(cols, rows[1:], cols, rows)["identical_within_tolerance"]


def test_gate_and_scoring_run_through_a_dbapi_cursor(con):
    """The runner passes a Snowflake cursor to the same gate and scoring code; a DuckDB cursor stands in for it here."""
    import yaml
    from fundlake.detection import score
    from fundlake.pipeline import run_gate_checks

    class Cur:
        def __init__(self, c):
            self.c = c

        def execute(self, sql, params=None):
            return self.c.execute(sql)

    gate = yaml.safe_load((ROOT / "contracts" / "gate.yaml").read_text())
    assert all(c["passed"] for c in run_gate_checks(Cur(con), gate))
    assert score(Cur(con), ROOT / "data" / "raw" / "answer_key.json")["_overall"]["true_positive"] == 156
