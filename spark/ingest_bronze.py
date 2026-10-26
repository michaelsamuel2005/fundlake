"""PySpark bronze ingestion: the Databricks variant of the DuckDB bronze layer in dbt/macros/dialect.sql.

Every source CSV is read as all-varchar and written with the same lineage columns (_source_file, _row_number), so a bronze
table built by Spark can be compared row for row with the one DuckDB builds. Two ways to run it:

  locally (tests/test_spark_parity.py):      ingest(spark, "data/raw", "/tmp/out")          -> Parquet files
  on Databricks (scripts/run_databricks.sh):  python ingest_bronze.py --raw /Volumes/... --schema workspace.fundlake
                                                                        --parity-out /Volumes/.../spark_bronze_parity.json
                                                                        -> Delta tables in Unity Catalog + a parity file

The parity file holds, for every table, the row count and a content fingerprint (SHA-256 of the rows, sorted, with
columns in name order); scripts/check_databricks_parity.py compares it with the DuckDB bronze layer."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

SOURCES = ["oms_trades", "oms_funds", "oms_counterparties", "custodian_positions", "custodian_cash_nav", "vendor_prices", "vendor_fx",
           "secmaster_securities", "secmaster_identifiers", "insurer_policies", "insurer_claims",
           "pm_funds", "pm_commitments", "pm_secondary_transfers", "pm_notices", "pm_notice_lines", "pm_nav_statements"]


def read_bronze(spark, raw_dir: str, name: str):
    from pyspark.sql import functions as F
    from pyspark.sql.window import Window

    df = (spark.read.option("header", True).option("inferSchema", False).option("multiLine", False).option("escape", '"')
          .csv(f"{raw_dir.rstrip('/')}/{name}.csv"))
    # One small file is read as one partition in file order; number the rows in that order.
    df = df.coalesce(1).withColumn("_mid", F.monotonically_increasing_id())
    return (df.withColumn("_row_number", F.row_number().over(Window.orderBy("_mid")).cast("long"))
              .withColumn("_source_file", F.lit(f"{name}.csv")).drop("_mid"))


def fingerprint(rows: list[dict]) -> str:
    """Order-independent content hash: columns in name order, None as empty, rows sorted."""
    if not rows:
        return hashlib.sha256(b"").hexdigest()
    cols = sorted(rows[0])
    lines = sorted("\x1f".join("" if r[c] is None else str(r[c]) for c in cols) for r in rows)
    return hashlib.sha256(("\x1f".join(cols) + "\n" + "\n".join(lines)).encode()).hexdigest()


def ingest(spark, raw_dir: str, out: str, as_tables: bool = False) -> dict[str, dict]:
    """Write each bronze table (Parquet under `out`, or Delta tables in schema `out` when as_tables) and return counts and fingerprints."""
    result = {}
    for name in SOURCES:
        df = read_bronze(spark, raw_dir, name)
        if as_tables:
            df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{out}.bronze_{name}")
            df = spark.table(f"{out}.bronze_{name}")
        else:
            df.write.mode("overwrite").parquet(str(Path(out) / f"bronze_{name}"))
        rows = [r.asDict() for r in df.collect()]
        result[name] = dict(rows=len(rows), fingerprint=fingerprint(rows),
                            row_numbers_ok=sorted(r["_row_number"] for r in rows) == list(range(1, len(rows) + 1)))
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--schema", help="write Delta tables into this catalog.schema (Databricks)")
    ap.add_argument("--out", help="write Parquet under this directory (local)")
    ap.add_argument("--parity-out", help="where to write the parity JSON")
    a = ap.parse_args()
    from pyspark.sql import SparkSession
    builder = SparkSession.builder.appName("fundlake-bronze")
    on_databricks = "DATABRICKS_RUNTIME_VERSION" in os.environ or Path("/databricks").exists()
    if not on_databricks:
        builder = builder.master("local[1]")
    spark = builder.getOrCreate()
    if a.schema:
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {a.schema}")
    res = ingest(spark, a.raw, a.schema or a.out, as_tables=bool(a.schema))
    doc = dict(engine="spark", spark_version=spark.version, databricks_runtime=os.environ.get("DATABRICKS_RUNTIME_VERSION"),
               target=a.schema or a.out, tables=res)
    print(json.dumps(doc, indent=1))
    if a.parity_out:
        Path(a.parity_out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.parity_out).write_text(json.dumps(doc, indent=1))


if __name__ == "__main__":
    main()
