#!/usr/bin/env bash
# Run fundlake's PySpark bronze ingestion on Databricks (Free Edition works: serverless, Unity Catalog) and check it
# against the DuckDB bronze layer. Prerequisites, once:
#   brew tap databricks/tap && brew install databricks          # Databricks CLI (0.250 or later)
#   databricks auth login --host https://<your-workspace>.cloud.databricks.com
#   python -m fundlake.pipeline                                  # builds the local DuckDB layer to compare against
# Then: bash scripts/run_databricks.sh     -> outputs/databricks_run.json
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
CATALOG="${CATALOG:-workspace}"
SCHEMA="${SCHEMA:-fundlake}"
command -v databricks >/dev/null || { echo "Install the Databricks CLI first: brew tap databricks/tap && brew install databricks"; exit 1; }
databricks current-user me >/dev/null || { echo "Log in first: databricks auth login --host https://<your-workspace>.cloud.databricks.com"; exit 1; }
[ -f data/fundlake.duckdb ] || python -m fundlake.pipeline

echo "1/5 schema and volume ($CATALOG.$SCHEMA, volume raw)"
databricks schemas create "$SCHEMA" "$CATALOG" >/dev/null 2>&1 || true
databricks volumes create "$CATALOG" "$SCHEMA" raw MANAGED >/dev/null 2>&1 || true

echo "2/5 upload the 17 source files"
for f in data/raw/*.csv; do
  databricks fs cp --overwrite "$f" "dbfs:/Volumes/$CATALOG/$SCHEMA/raw/$(basename "$f")" >/dev/null
done

echo "3/5 deploy the bundle"
databricks bundle deploy -t dev --var="catalog=$CATALOG" --var="schema=$SCHEMA"

echo "4/5 run the ingestion job on serverless compute (a few minutes the first time)"
databricks bundle run fundlake_bronze -t dev --var="catalog=$CATALOG" --var="schema=$SCHEMA"

echo "5/5 fetch the parity file and compare with DuckDB"
mkdir -p outputs
databricks fs cp --overwrite "dbfs:/Volumes/$CATALOG/$SCHEMA/raw/_parity/spark_bronze_parity.json" outputs/spark_bronze_parity.json >/dev/null
python scripts/check_databricks_parity.py outputs/spark_bronze_parity.json outputs/databricks_run.json
echo "Done: outputs/databricks_run.json"
