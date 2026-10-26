"""Reproduce everything: generate synthetic data, run the pipeline, score controls, evaluate the agent, write outputs/results.json."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import duckdb  # noqa: E402

from fundlake import pipeline as P  # noqa: E402
from fundlake.agent.agent import ScriptedModel  # noqa: E402
from fundlake.agent.eval import evaluate  # noqa: E402
from fundlake.detection import score  # noqa: E402
from fundlake.synth import generate  # noqa: E402


def main():
    raw, db = ROOT / "data/raw", ROOT / "data/fundlake.duckdb"
    generate(raw)
    db.unlink(missing_ok=True)
    info = P.run_pipeline(P.Config(raw, db))
    con = duckdb.connect(str(db), read_only=True)
    q = lambda s: con.execute(s).fetchall()
    det = score(con, raw / "answer_key.json")
    ev = evaluate(db, raw, raw / "answer_key.json", ScriptedModel().build())
    nav = q("select count(*), count(*) filter (is_breach), round(max(abs(diff_bps)),1), round(max(abs(diff_gbp))) from gold.fact_nav")[0]
    key = json.loads((raw / "answer_key.json").read_text())
    pm = con.execute("select * from gold.mart_pm_performance").fetchdf()
    navk = con.execute("select investor_id || ':' || pm_fund_id as k, nav_asof from gold.pm_nav_asof").fetchdf().set_index("k").nav_asof
    lag = {k: navk[k] / v - 1 for k, v in key["pm_true_nav_asof"].items()}
    sec = pm[pm.origin == "secondary"].iloc[0]
    brk = con.execute("select count(*), max(contributions_diff), min(period_end) from gold.pm_recon_capital_account where is_break").fetchone()
    private = dict(funds=int(pm.pm_fund_id.nunique()), commitments=len(pm), strategies=sorted(pm.strategy.unique().tolist()),
                   currencies=sorted(pm.currency.unique().tolist()), nav_gbp=round(float(pm.nav_gbp.sum()), 2), unfunded_gbp=round(float(pm.unfunded_gbp.sum()), 2),
                   tvpi_range=[round(float(pm.tvpi.min()), 2), round(float(pm.tvpi.max()), 2)],
                   irr_range_primary=[round(float(pm[pm.origin == "primary"].irr.min()), 4), round(float(pm[pm.origin == "primary"].irr.max()), 4)],
                   secondary=dict(pm_fund_id=sec.pm_fund_id, tvpi_on_purchase_price=round(float(sec.tvpi), 2), gp_basis_tvpi=round(float(sec.gp_basis_tvpi), 2),
                                  irr=round(float(sec.irr), 4)),
                   unreconciled=pm[~pm.is_reconciled][["investor_id", "pm_fund_id"]].to_dict("records"),
                   capital_account_breaks=dict(rows=brk[0], amount=brk[1], from_period=str(brk[2])),
                   statement_lag_days=int(con.execute("select max(statement_lag_days) from gold.pm_nav_asof").fetchone()[0]),
                   lag_cost=dict(mean_abs_pct=round(100 * sum(abs(v) for v in lag.values()) / len(lag), 2), max_abs_pct=round(100 * max(abs(v) for v in lag.values()), 2)),
                   metrics_match_answer_key=True)
    for k, e in key["pm_expected"].items():
        r = pm[(pm.investor_id + ":" + pm.pm_fund_id) == k].iloc[0]
        if abs(r.tvpi - e["tvpi"]) > 1e-9 or abs(r.irr - e["irr"]) > 1e-6:
            private["metrics_match_answer_key"] = False
    res = dict(build_id=info["audit"]["build_id"], gold_sha256=info["audit"]["gold_sha256"], row_counts=info["audit"]["row_counts"],
               gate=info["quality_gate"]["checks"], dbt=dict(silver=info["dbt_silver"]["summary"], gold=info["dbt_gold"]["summary"]),
               detection={k: {x: y for x, y in v.items() if x in ("planted", "detected", "true_positive", "recall", "precision")} if k != "_overall" else v for k, v in det.items()},
               nav=dict(fund_days=nav[0], breaches=nav[1], max_abs_bps=nav[2], max_abs_gbp=nav[3]), private_markets=private,
               agent_eval_scripted={k: v for k, v in ev.items() if k != "rows"})
    for name in ("airflow_dag_test", "airflow_dag_test_gate_fail", "spark_local_parity", "databricks_run", "snowflake_run"):
        f = ROOT / "outputs" / f"{name}.json"
        res[name] = json.loads(f.read_text()) if f.exists() else None
    (ROOT / "outputs").mkdir(exist_ok=True)
    (ROOT / "outputs/results.json").write_text(json.dumps(res, indent=1, default=str))
    (ROOT / "outputs/agent_eval.json").write_text(json.dumps(ev, indent=1))
    subprocess.run([sys.executable, str(ROOT / "scripts/gen_dictionary.py"), str(db)], check=True)
    print(json.dumps({k: res[k] for k in ("build_id", "nav", "private_markets")}, indent=1, default=str), "\ndetection overall:", res["detection"]["_overall"], "\nagent:", res["agent_eval_scripted"])


if __name__ == "__main__":
    main()
