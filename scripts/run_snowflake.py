"""Run fundlake on Snowflake and compare it with the DuckDB build.

Once:
  pip install -e ".[snowflake]" "snowflake-connector-python[pandas]"
  python scripts/run_snowflake.py keygen
      -> creates a key pair in ~/.fundlake and prints the setup SQL; paste it into a Snowsight worksheet and run it
         as ACCOUNTADMIN (a service user that signs in with the key, so no password or MFA prompt is involved)
Then:
  python scripts/run_snowflake.py run --account <orgname-accountname>
      -> loads the 17 extracts (PUT + COPY INTO), dbt bronze/silver on Snowflake, the quality gate, dbt gold, row-level
         security and masking checked role by role, the planted faults scored, and every gold table compared with the
         DuckDB build. Results: outputs/snowflake_run.json. Cost: an X-Small warehouse for a few minutes.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import decimal
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
HOME = Path.home() / ".fundlake"
KEY = HOME / "snowflake_rsa_key.p8"
USER, ROLE, WH, DB = "FUNDLAKE_SVC", "FUNDLAKE_ROLE", "FUNDLAKE_WH", "FUNDLAKE"
ACCESS = yaml.safe_load((ROOT / "contracts" / "access.yaml").read_text())["roles"]
TEST_ROLES = {r.upper(): regions for r, regions in ACCESS.items()}

SETUP_SQL = """-- fundlake on Snowflake: paste into a Snowsight worksheet and run all (as ACCOUNTADMIN). Safe to re-run.
USE ROLE ACCOUNTADMIN;
CREATE WAREHOUSE IF NOT EXISTS FUNDLAKE_WH WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE;
CREATE ROLE IF NOT EXISTS FUNDLAKE_ROLE;
CREATE DATABASE IF NOT EXISTS FUNDLAKE;
GRANT OWNERSHIP ON DATABASE FUNDLAKE TO ROLE FUNDLAKE_ROLE COPY CURRENT GRANTS;
GRANT USAGE, OPERATE ON WAREHOUSE FUNDLAKE_WH TO ROLE FUNDLAKE_ROLE;
GRANT ROLE FUNDLAKE_ROLE TO ROLE SYSADMIN;
CREATE USER IF NOT EXISTS FUNDLAKE_SVC TYPE = SERVICE DEFAULT_ROLE = FUNDLAKE_ROLE DEFAULT_WAREHOUSE = FUNDLAKE_WH;
ALTER USER FUNDLAKE_SVC SET RSA_PUBLIC_KEY = '{pub}';
GRANT ROLE FUNDLAKE_ROLE TO USER FUNDLAKE_SVC;
-- roles used to test row-level security on the client view (row access policies need Enterprise edition)
{roles}
"""


def keygen() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    HOME.mkdir(mode=0o700, exist_ok=True)
    if not KEY.exists():
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        KEY.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        os.chmod(KEY, 0o600)
    priv = serialization.load_pem_private_key(KEY.read_bytes(), password=None)
    pub = priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    body = "".join(pub.strip().splitlines()[1:-1])
    roles = "\n".join(f"CREATE ROLE IF NOT EXISTS {r};\nGRANT ROLE {r} TO USER FUNDLAKE_SVC;\nGRANT USAGE ON WAREHOUSE FUNDLAKE_WH TO ROLE {r};"
                      for r in TEST_ROLES)
    sql = SETUP_SQL.format(pub=body, roles=roles)
    (HOME / "snowflake_setup.sql").write_text(sql)
    print(sql)
    print(f"-- also saved to {HOME / 'snowflake_setup.sql'}; the private key stays in {KEY}")


class Cursor:
    """The subset of a DB-API cursor the fundlake checks use: execute(sql).fetchone()/fetchall()."""
    def __init__(self, conn):
        self.cur = conn.cursor()

    def execute(self, sql, params=None):
        self.cur.execute(sql, params) if params else self.cur.execute(sql)
        return self.cur


def connect(account: str):
    import snowflake.connector
    from cryptography.hazmat.primitives import serialization
    pkey = serialization.load_pem_private_key(KEY.read_bytes(), password=None)
    der = pkey.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return snowflake.connector.connect(account=account, user=USER, private_key=der, role=ROLE, warehouse=WH, database=DB)


def write_profile(account: str) -> Path:
    prof = {"fundlake": {"target": "snowflake", "outputs": {"snowflake": {
        "type": "snowflake", "account": account, "user": USER, "private_key_path": str(KEY), "role": ROLE,
        "database": DB, "warehouse": WH, "schema": "PUBLIC", "threads": 4}}}}
    (HOME / "profiles.yml").write_text(yaml.safe_dump(prof))
    return HOME


def dbt(select: str, profiles: Path, work: Path) -> dict:
    env = dict(os.environ, DBT_SEND_ANONYMOUS_USAGE_STATS="false")
    cmd = ["dbt", "build", "--project-dir", str(ROOT / "dbt"), "--profiles-dir", str(profiles), "--target", "snowflake", "--select", select,
           "--indirect-selection", "buildable", "--target-path", str(work / "target"), "--log-path", str(work / "logs"), "--no-use-colors"]
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ROOT / "dbt")
    rr = json.loads((work / "target" / "run_results.json").read_text()) if (work / "target" / "run_results.json").exists() else {"results": []}
    summary: dict = {}
    for r in rr["results"]:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    errors = [dict(node=r["unique_id"], status=r["status"], message=(r.get("message") or "")[:300]) for r in rr["results"] if r["status"] in ("error", "fail")]
    return dict(returncode=p.returncode, summary=summary, errors=errors, tail=p.stdout[-3000:] if p.returncode else "")


LOAD_PREAMBLE = [
    "CREATE SCHEMA IF NOT EXISTS RAW",
    "CREATE OR REPLACE FILE FORMAT RAW.CSV_FMT TYPE = CSV SKIP_HEADER = 1 FIELD_OPTIONALLY_ENCLOSED_BY = '\"' NULL_IF = ('') EMPTY_FIELD_AS_NULL = TRUE",
    "CREATE OR REPLACE STAGE RAW.LANDING FILE_FORMAT = RAW.CSV_FMT",
]


def load_plan(raw: Path) -> list[dict]:
    """Per source file: the PUT, CREATE TABLE, COPY INTO and row-numbering statements, and the expected row count."""
    plan = []
    for f in sorted(raw.glob("*.csv")):
        name, t = f.stem, f.stem.upper()
        with f.open(newline="") as fh:
            reader = csv.reader(fh)
            cols = next(reader)
            n = sum(1 for _ in reader)
        plan.append(dict(name=name, rows=n, put=f"PUT 'file://{f.resolve().as_posix()}' @RAW.LANDING AUTO_COMPRESS = TRUE OVERWRITE = TRUE", sql=[
            f"CREATE OR REPLACE TABLE RAW.{t} ({', '.join(c + ' VARCHAR' for c in cols)}, _SOURCE_FILE VARCHAR, _ROW_NUMBER NUMBER)",
            f"COPY INTO RAW.{t} FROM (SELECT {', '.join(f'${i + 1}' for i in range(len(cols)))}, '{name}.csv', METADATA$FILE_ROW_NUMBER "
            f"FROM @RAW.LANDING/{name}.csv.gz) FILE_FORMAT = (FORMAT_NAME = RAW.CSV_FMT) ON_ERROR = ABORT_STATEMENT FORCE = TRUE",
            # number data rows from 1, as DuckDB does, whatever the header handling of METADATA$FILE_ROW_NUMBER
            f"UPDATE RAW.{t} SET _ROW_NUMBER = _ROW_NUMBER - (SELECT MIN(_ROW_NUMBER) FROM RAW.{t}) + 1",
        ]))
    return plan


def load(cur, raw: Path) -> dict:
    for s in LOAD_PREAMBLE:
        cur.execute(s)
    counts = {}
    for p in load_plan(raw):
        cur.execute(p["put"])
        for s in p["sql"]:
            cur.execute(s)
        got = cur.execute(f"SELECT COUNT(*) FROM RAW.{p['name'].upper()}").fetchone()[0]
        counts[p["name"]] = dict(csv_rows=p["rows"], loaded=int(got), ok=int(got) == p["rows"])
    return counts


def apply_rls(cur) -> dict:
    """Row access and masking policies on the client view, then each analyst role queries it."""
    out: dict = {"applied": False}
    try:
        cur.execute("CREATE SCHEMA IF NOT EXISTS SECURITY")
        cur.execute("CREATE OR REPLACE TABLE SECURITY.REGION_ACCESS (role_name VARCHAR, region VARCHAR)")
        vals = ", ".join(f"('{r}', '{reg}')" for r, regs in TEST_ROLES.items() for reg in regs)
        cur.execute(f"INSERT INTO SECURITY.REGION_ACCESS VALUES {vals}")
        try:
            cur.execute("ALTER TABLE GOLD.CLIENT_POLICY_VIEW DROP ALL ROW ACCESS POLICIES")
        except Exception:
            pass
        cur.execute("CREATE OR REPLACE ROW ACCESS POLICY SECURITY.REGION_POLICY AS (row_region VARCHAR) RETURNS BOOLEAN -> "
                    "CURRENT_ROLE() = 'FUNDLAKE_ROLE' OR EXISTS (SELECT 1 FROM SECURITY.REGION_ACCESS a "
                    "WHERE a.role_name = CURRENT_ROLE() AND (a.region = '*' OR a.region = row_region))")
        cur.execute("ALTER TABLE GOLD.CLIENT_POLICY_VIEW ADD ROW ACCESS POLICY SECURITY.REGION_POLICY ON (region)")
        cur.execute("CREATE OR REPLACE MASKING POLICY SECURITY.CLIENT_KEY_MASK AS (v VARCHAR) RETURNS VARCHAR -> "
                    "CASE WHEN CURRENT_ROLE() IN ('DATA_STEWARD', 'FUNDLAKE_ROLE') THEN v ELSE SHA2(v) END")
        cur.execute("ALTER TABLE GOLD.CLIENT_POLICY_VIEW MODIFY COLUMN client_key SET MASKING POLICY SECURITY.CLIENT_KEY_MASK")
        for r in TEST_ROLES:
            cur.execute(f"GRANT USAGE ON DATABASE {DB} TO ROLE {r}")
            cur.execute(f"GRANT USAGE ON SCHEMA {DB}.GOLD TO ROLE {r}")
            cur.execute(f"GRANT SELECT ON TABLE {DB}.GOLD.CLIENT_POLICY_VIEW TO ROLE {r}")
        out["applied"] = True
        full = dict(cur.execute("SELECT region, COUNT(*) FROM GOLD.CLIENT_POLICY_VIEW GROUP BY 1").fetchall())
        raw_keys = {k for (k,) in cur.execute("SELECT client_key FROM GOLD.CLIENT_POLICY_VIEW").fetchall()}
        roles = {}
        for r, regions in TEST_ROLES.items():
            cur.execute(f"USE ROLE {r}")
            seen = dict(cur.execute(f"SELECT region, COUNT(*) FROM {DB}.GOLD.CLIENT_POLICY_VIEW GROUP BY 1").fetchall())
            keys = {k for (k,) in cur.execute(f"SELECT client_key FROM {DB}.GOLD.CLIENT_POLICY_VIEW").fetchall()}
            want = {reg: full.get(reg, 0) for reg in (full if "*" in regions else regions) if full.get(reg, 0)}
            masked = not (keys & raw_keys)
            roles[r] = dict(rows=sum(seen.values()), regions=sorted(seen), expected_regions=sorted(want), rows_ok=seen == want,
                            client_key_masked=masked, masking_ok=masked == (r != "DATA_STEWARD"))
            cur.execute(f"USE ROLE {ROLE}")
        out["roles"] = roles
        out["all_ok"] = all(v["rows_ok"] and v["masking_ok"] for v in roles.values())
    except Exception as e:                       # Standard edition has no row access policies
        out["error"] = str(e)[:400]
        try:
            cur.execute(f"USE ROLE {ROLE}")
        except Exception:
            pass
    return out


def _norm(v):
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, decimal.Decimal)):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()[:19] if isinstance(v, dt.datetime) else v.isoformat()
    return str(v)


def table_rows(cursor, sql: str) -> tuple[list[str], list[tuple]]:
    cur = cursor.execute(sql)
    cols = [d[0].lower() for d in cur.description]
    return cols, [tuple(_norm(x) for x in r) for r in cur.fetchall()]


def compare_tables(sf_cols, sf_rows, dk_cols, dk_rows) -> dict:
    """Same rows? Align by the non-float columns plus floats rounded to 4 dp, then measure the largest float difference.
    Floats agree when within one millionth (relative): the two engines sum in different orders."""
    if set(sf_cols) != set(dk_cols):
        return dict(columns_match=False, snowflake_only=sorted(set(sf_cols) - set(dk_cols)), duckdb_only=sorted(set(dk_cols) - set(sf_cols)))
    order = [dk_cols.index(c) for c in sf_cols]
    dk_rows = [tuple(r[i] for i in order) for r in dk_rows]
    key = lambda r: tuple((round(x, 4) + 0.0) if isinstance(x, float) else ("" if x is None else str(x)) for x in r)
    a, b = sorted(sf_rows, key=key), sorted(dk_rows, key=key)
    if len(a) != len(b):
        return dict(columns_match=True, rows_snowflake=len(a), rows_duckdb=len(b), identical_within_tolerance=False)
    max_diff, mismatches = 0.0, 0
    for ra, rb in zip(a, b):
        for x, y in zip(ra, rb):
            if isinstance(x, float) and isinstance(y, float):
                d = abs(x - y) / max(1.0, abs(y))
                max_diff = max(max_diff, d)
                mismatches += d > 1e-6
            elif x != y:
                mismatches += 1
    return dict(columns_match=True, rows=len(a), mismatched_values=mismatches, max_relative_float_diff=max_diff,
                identical_within_tolerance=mismatches == 0)


def run(account: str) -> dict:
    from fundlake import pipeline as P
    from fundlake.contracts import validate_all
    from fundlake.detection import score
    import duckdb

    work = HOME / "work"
    work.mkdir(parents=True, exist_ok=True)
    raw = ROOT / "data" / "raw"
    res: dict = dict(account=account, started_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), steps={})
    t0 = time.time()
    conn = connect(account)
    cur = Cursor(conn)
    res["snowflake_version"], res["role"] = cur.execute("SELECT CURRENT_VERSION(), CURRENT_ROLE()").fetchone()
    res["edition_note"] = "row access policies need Enterprise edition; see steps.rls"
    bad = {k: v for k, v in validate_all(raw).items() if v}
    res["steps"]["contracts"] = dict(sources=17, violations=len(bad))
    s = time.time(); res["steps"]["load"] = dict(tables=load(cur, raw), seconds=round(time.time() - s, 1))
    profiles = write_profile(account)
    s = time.time(); silver = dbt("tag:bronze tag:silver", profiles, work); silver["seconds"] = round(time.time() - s, 1)
    res["steps"]["dbt_silver"] = silver
    if silver["errors"] or silver["returncode"]:
        return finish(res, t0, "dbt_silver failed")
    gate = yaml.safe_load((ROOT / "contracts" / "gate.yaml").read_text())
    checks = P.run_gate_checks(cur, gate)
    res["steps"]["quality_gate"] = dict(checks=checks, passed=all(c["passed"] for c in checks))
    if not res["steps"]["quality_gate"]["passed"]:
        return finish(res, t0, "quality gate stopped the run")
    s = time.time(); gold = dbt("tag:gold", profiles, work); gold["seconds"] = round(time.time() - s, 1)
    res["steps"]["dbt_gold"] = gold
    if gold["errors"] or gold["returncode"]:
        return finish(res, t0, "dbt_gold failed")
    res["steps"]["rls"] = apply_rls(cur)
    sc = score(cur, raw / "answer_key.json")
    res["steps"]["detection"] = dict(overall=sc["_overall"], per_class={k: dict(planted=v["planted"], found=v["true_positive"], false_positives=len(v["false_positive"]))
                                                                    for k, v in sc.items() if k != "_overall"})
    duck = ROOT / "data" / "fundlake.duckdb"
    if not duck.exists():
        P.run_pipeline(P.Config(raw, duck))
    dcon = duckdb.connect(str(duck), read_only=True)
    parity = {}
    for t in P.GOLD_TABLES:
        sc_cols, sc_rows = table_rows(cur, f"SELECT * FROM GOLD.{t.upper()}")
        dk_cols, dk_rows = table_rows(dcon, f"select * from gold.{t}")
        parity[t] = compare_tables(sc_cols, sc_rows, dk_cols, dk_rows)
    res["steps"]["parity_with_duckdb"] = dict(tables=len(parity), identical=sum(v.get("identical_within_tolerance", False) for v in parity.values()),
                                              detail=parity)
    return finish(res, t0, None)


def finish(res: dict, t0: float, stopped: str | None) -> dict:
    res["stopped"] = stopped
    res["total_seconds"] = round(time.time() - t0, 1)
    out = ROOT / "outputs" / "snowflake_run.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=str))
    summary = dict(stopped=stopped, load_ok=all(v["ok"] for v in res["steps"].get("load", {}).get("tables", {}).values()) if "load" in res["steps"] else None,
                   dbt_silver=res["steps"].get("dbt_silver", {}).get("summary"), dbt_gold=res["steps"].get("dbt_gold", {}).get("summary"),
                   gate_passed=res["steps"].get("quality_gate", {}).get("passed"), detection=res["steps"].get("detection", {}).get("overall"),
                   rls_ok=res["steps"].get("rls", {}).get("all_ok"),
                   parity=None if "parity_with_duckdb" not in res["steps"] else f"{res['steps']['parity_with_duckdb']['identical']} of {res['steps']['parity_with_duckdb']['tables']} gold tables identical")
    print(json.dumps(summary, indent=1))
    print(f"Full results: {out}")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen")
    r = sub.add_parser("run")
    r.add_argument("--account", required=True, help="account identifier, e.g. myorg-myaccount (Snowsight: Admin > Accounts, or the account menu)")
    a = ap.parse_args()
    if a.cmd == "keygen":
        keygen()
    else:
        sys.exit(0 if run(a.account).get("stopped") is None else 1)
