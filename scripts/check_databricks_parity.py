"""Compare a Spark bronze parity file (from Databricks or a local Spark run) with the DuckDB bronze layer, table by table:
row counts, row numbering and a content fingerprint of every row. Writes outputs/databricks_run.json (or the path given)."""
import json
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spark"))
from ingest_bronze import SOURCES, fingerprint  # noqa: E402


def duckdb_bronze(db: Path) -> dict:
    con = duckdb.connect(str(db), read_only=True)
    out = {}
    for name in SOURCES:
        cur = con.execute(f"select * from bronze.bronze_{name}")
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        out[name] = dict(rows=len(rows), fingerprint=fingerprint(rows))
    return out


def compare(spark_doc: dict, db: Path) -> dict:
    duck = duckdb_bronze(db)
    tables = {}
    for name in SOURCES:
        s, d = spark_doc["tables"].get(name), duck[name]
        tables[name] = dict(spark_rows=s and s["rows"], duckdb_rows=d["rows"], row_numbers_ok=bool(s and s["row_numbers_ok"]),
                            identical=bool(s and s["fingerprint"] == d["fingerprint"]))
    return dict(engine=spark_doc.get("engine"), spark_version=spark_doc.get("spark_version"),
                databricks_runtime=spark_doc.get("databricks_runtime"), target=spark_doc.get("target"),
                tables_compared=len(tables), tables_identical=sum(t["identical"] for t in tables.values()),
                all_identical=all(t["identical"] and t["row_numbers_ok"] for t in tables.values()), tables=tables)


if __name__ == "__main__":
    parity = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "outputs" / "spark_bronze_parity.json"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "outputs" / "databricks_run.json"
    db = ROOT / "data" / "fundlake.duckdb"
    if not db.exists():
        sys.exit("build the DuckDB layer first: python -m fundlake.pipeline")
    res = compare(json.loads(parity.read_text()), db)
    out.write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k != "tables"}, indent=1))
    sys.exit(0 if res["all_identical"] else 1)
