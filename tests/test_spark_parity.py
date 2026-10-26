"""Parity of the PySpark bronze step with the DuckDB bronze layer: same rows, same numbering, same content in every one of
the 17 tables. Skipped where PySpark is not installed; run it with the Spark virtual environment (see README)."""
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyspark")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spark"))
sys.path.insert(0, str(ROOT / "scripts"))


def test_spark_bronze_is_identical_to_duckdb_bronze(built, tmp_path):
    from pyspark.sql import SparkSession
    import ingest_bronze as I
    import check_databricks_parity as C
    spark = SparkSession.builder.master("local[1]").appName("parity").getOrCreate()
    res = I.ingest(spark, str(ROOT / "data" / "raw"), str(tmp_path))
    doc = dict(engine="spark", spark_version=spark.version, tables=res)
    cmp = C.compare(doc, built["db"])
    assert cmp["tables_compared"] == 17 and cmp["all_identical"], json.dumps(cmp["tables"], indent=1)
