"""The Snowflake path is exercised OFFLINE: dbt renders every model for the snowflake target (no connection), and sqlglot parses the
result with its Snowflake dialect. This proves the adapter-dispatched macros resolve to Snowflake syntax. It does not execute anything
on Snowflake."""
import os
import subprocess
from pathlib import Path

import pytest

sqlglot = pytest.importorskip("sqlglot")
pytest.importorskip("dbt.adapters.snowflake")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    out = tmp_path_factory.mktemp("sf")
    env = dict(os.environ, DBT_SEND_ANONYMOUS_USAGE_STATS="false", FUNDLAKE_TARGET="snowflake")
    p = subprocess.run(["dbt", "compile", "--project-dir", str(ROOT / "dbt"), "--profiles-dir", str(ROOT / "dbt"), "--target", "snowflake",
                        "--no-populate-cache", "--target-path", str(out), "--log-path", str(out / "logs"), "--quiet"], capture_output=True, text=True, env=env, cwd=ROOT / "dbt")
    assert p.returncode == 0, p.stdout[-1500:]
    return out / "compiled" / "fundlake"


def test_all_models_parse_as_snowflake_sql(compiled):
    files = sorted((compiled / "models").rglob("*.sql"))
    assert len(files) >= 40
    for f in files:
        sqlglot.parse(f.read_text(), read="snowflake")


def test_dispatch_resolves_to_snowflake_syntax(compiled):
    g = lambda n: next((compiled / "models").rglob(n)).read_text().lower()
    assert "listagg(" in g("lineage_position.sql") and "string_agg" not in g("lineage_position.sql")
    assert "ignore nulls) over" not in g("fact_price.sql") and ") ignore nulls over" in g("fact_price.sql")
    assert "datediff(day" in g("mart_loss_ratio.sql") and "date_diff(" not in g("mart_loss_ratio.sql")
    assert "generator(rowcount" in g("dim_date.sql") and "generate_series" not in g("dim_date.sql")
    assert "{{ source" not in g("bronze_oms_trades.sql") and "raw.oms_trades" in g("bronze_oms_trades.sql") and "read_csv" not in g("bronze_oms_trades.sql")
    assert g("pm_irr.sql").count("generator(rowcount") == 2 and "generate_series" not in g("pm_irr.sql") and "datediff(day" in g("pm_irr.sql")


def test_duckdb_path_uses_duckdb_syntax(tmp_path):
    env = dict(os.environ, DBT_SEND_ANONYMOUS_USAGE_STATS="false", FUNDLAKE_DB=str(tmp_path / "x.duckdb"))
    p = subprocess.run(["dbt", "compile", "--project-dir", str(ROOT / "dbt"), "--profiles-dir", str(ROOT / "dbt"), "--target-path", str(tmp_path / "t"),
                        "--log-path", str(tmp_path / "l"), "--select", "lineage_position", "bronze_oms_trades", "--quiet"], capture_output=True, text=True, env=env, cwd=ROOT / "dbt")
    assert p.returncode == 0, p.stdout[-800:]
    c = (tmp_path / "t/compiled/fundlake/models/gold/lineage_position.sql").read_text().lower()
    assert "string_agg(" in c
    assert "read_csv(" in (tmp_path / "t/compiled/fundlake/models/bronze/bronze_oms_trades.sql").read_text().lower()
