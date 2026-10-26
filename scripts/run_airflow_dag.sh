#!/usr/bin/env bash
# Run the fundlake DAG end to end with `airflow dags test` (no scheduler needed) and save a summary to outputs/.
# Airflow lives in its own virtual environment; dbt is found on PATH (or set FUNDLAKE_DBT).
#   uv venv .venv-airflow --python 3.12 && uv pip install --python .venv-airflow/bin/python "apache-airflow==3.1.8" duckdb pandas pyyaml pydantic
#   uv pip install --python .venv-airflow/bin/python -e . --no-deps
#   AIRFLOW_BIN=.venv-airflow/bin/airflow bash scripts/run_airflow_dag.sh
#   Add FAIL_GATE=1 to run it on a corrupted copy of the data and show the gate stopping the run before gold.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AIRFLOW_BIN="${AIRFLOW_BIN:-airflow}"
export AIRFLOW_HOME="${AIRFLOW_HOME:-$(mktemp -d)}"
export AIRFLOW__CORE__DAGS_FOLDER="$ROOT/airflow/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export AIRFLOW__LOGGING__LOGGING_LEVEL=WARNING
export DBT_SEND_ANONYMOUS_USAGE_STATS=false
OUT="$ROOT/outputs/airflow_dag_test.json"
FUNDLAKE_DB_PATH="${FUNDLAKE_DB:-$ROOT/data/fundlake.duckdb}"
if [ "${FAIL_GATE:-0}" = "1" ]; then
  export FUNDLAKE_RAW="$(mktemp -d)/raw"; cp -r "$ROOT/data/raw" "$FUNDLAKE_RAW"
  export FUNDLAKE_DB="$(mktemp -d)/gate_fail.duckdb"; FUNDLAKE_DB_PATH="$FUNDLAKE_DB"
  python3 - "$FUNDLAKE_RAW/secmaster_identifiers.csv" <<'PY'
import sys, pandas as pd
p = sys.argv[1]; d = pd.read_csv(p, dtype=str)
lost = d[d.id_type == "ISIN"].security_id.unique()[:25]
d[~((d.id_type == "ISIN") & d.security_id.isin(lost))].to_csv(p, index=False)
PY
  OUT="$ROOT/outputs/airflow_dag_test_gate_fail.json"
fi
"$AIRFLOW_BIN" db migrate > /dev/null
set +e
"$AIRFLOW_BIN" dags test fundlake_daily 2026-10-26 > "$AIRFLOW_HOME/dags_test.log" 2>&1
RC=$?
set -e
python3 - "$AIRFLOW_HOME/airflow.db" "$OUT" "$RC" "$FUNDLAKE_DB_PATH" <<'PY'
import json, sqlite3, sys
db, out, rc, fl_db = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
c = sqlite3.connect(db)
run = c.execute("select run_id, state, start_date, end_date from dag_run where dag_id='fundlake_daily' order by start_date desc limit 1").fetchone()
tasks = c.execute("select task_id, state, start_date, end_date from task_instance where dag_id='fundlake_daily' and run_id=? order by start_date",
                  [run[0]]).fetchall()
summary = dict(command="airflow dags test fundlake_daily 2026-10-26", exit_code=rc, dag_run_id=run[0], dag_run_state=run[1],
               started=run[2], ended=run[3], tasks=[dict(task_id=t[0], state=t[1], started=t[2], ended=t[3]) for t in tasks])
try:
    import duckdb
    rec = duckdb.connect(fl_db, read_only=True).execute("select record from audit.build_runs order by built_at_utc desc limit 1").fetchone()
    if rec:
        r = json.loads(rec[0])
        summary["audit_record"] = {k: r[k] for k in ("build_id", "gold_sha256", "orchestrator", "dbt_tests")}
except Exception as e:                       # no audit record when the gate stopped the run
    summary["audit_record"] = None
json.dump(summary, open(out, "w"), indent=1)
print(json.dumps(summary, indent=1))
PY
