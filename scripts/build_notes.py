"""Build the two PDF notes (client note, technical note) from the built database and outputs/results.json."""
import json
from pathlib import Path

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
R = json.loads((ROOT / "outputs/results.json").read_text())
con = duckdb.connect(str(ROOT / "data/fundlake.duckdb"), read_only=True)
q = lambda s: con.execute(s).fetchall()
f0 = lambda x: f"{x:,.0f}"

# ---- numbers -------------------------------------------------------------------------------------------------------------
fd, br, mxbps, mxgbp = R["nav"]["fund_days"], R["nav"]["breaches"], R["nav"]["max_abs_bps"], R["nav"]["max_abs_gbp"]
unexpl = q("select count(*) from gold.fact_nav where is_breach and n_carried_prices=0 and n_stale_prices=0 and not stale_fx_day")[0][0]
bdays = q("select count(*) from gold.recon_position_breaks")[0][0]
eps = q("select count(distinct (fund_id, security_id, episode)) from gold.recon_position_breaks")[0][0]
explained = q("select count(distinct (fund_id, security_id, episode)) from gold.recon_position_breaks where cause_hint='trade_not_reflected_at_custodian'")[0][0]
naive = q("""select count(*) from silver.silver_custodian_positions p left join silver.silver_identifiers x on x.id_type='ISIN' and x.id_value=p.isin
             and x.valid_to = date '9999-12-31' where x.security_id is null""")[0][0]
n_raw = q("select count(*) from bronze.bronze_oms_trades")[0][0]; n_dup = q("select count(*) from silver.dq_findings where finding_type='duplicate_trade'")[0][0]
n_cl = q("select count(*) from silver.silver_claims")[0][0]; n_bad = q("select count(*) from silver.silver_claims where invalid_reason is not null")[0][0]
n_cdup = q("select count(*) from silver.dq_findings where finding_type='duplicate_claim'")[0][0]
stalefx_mx = q("select round(max(abs(diff_bps)),1) from gold.fact_nav where stale_fx_day")[0][0]
PM = R['private_markets']
n_classes = len([k for k in R['detection'] if k != '_overall'])
lr = q("select product, round(sum(incurred)/sum(earned_premium)*100) from gold.mart_loss_ratio group by 1 order by 2 desc")
det = R["detection"]; ev = R["agent_eval_scripted"]

# ---- chart ---------------------------------------------------------------------------------------------------------------
df = con.execute("select position_date d, diff_bps, n_carried_prices c, n_stale_prices s, stale_fx_day fx, is_breach b from gold.fact_nav where fund_id='F03' order by 1").fetchdf()
df["d"] = pd.to_datetime(df.d)
fig, ax = plt.subplots(figsize=(7.2, 2.5), dpi=200)
ax.axhspan(-0.5, 0.5, color="#dfe9e1", label="tolerance ±0.5 bp", zorder=0)
ax.plot(df.d, df.diff_bps, color="#2b4a6f", lw=1.2, zorder=2)
for cond, col, lab in ((df.fx, "#b5542b", "stale FX day"), (df.c > 0, "#7a5aa6", "carried price"), (df.s > 0, "#c9a227", "stale price")):
    m = df[cond]
    ax.scatter(m.d, m.diff_bps, s=16, color=col, zorder=3, label=lab)
ax.set_ylabel("NAV vs administrator (bp)", fontsize=7); ax.tick_params(labelsize=7)
for s in ("top", "right"): ax.spines[s].set_visible(False)
ax.legend(fontsize=6.5, frameon=False, ncol=4, loc="upper left")
fig.tight_layout(); fig.savefig(ROOT / "docs/nav_f03.png"); plt.close(fig)

CSS = """
@page { size: A4; margin: 14mm 15mm; }
body { font-family: Arial, Helvetica, sans-serif; font-size: 9.2pt; line-height: 1.32; color: #1b1f24; }
h1 { font-size: 15pt; margin: 0 0 2px; color: #14304f; } h2 { font-size: 10.5pt; margin: 9px 0 3px; color: #14304f; border-bottom: 1px solid #c9d3de; padding-bottom: 1px; }
.sub { color: #51606f; font-size: 8.5pt; margin-bottom: 6px; } .tag { background: #fff4d6; border: 1px solid #e3c870; padding: 3px 6px; font-size: 8pt; margin: 4px 0 6px; }
table { border-collapse: collapse; width: 100%; font-size: 8.3pt; margin: 3px 0; } th { background: #14304f; color: #fff; text-align: left; padding: 2px 5px; font-weight: 600; }
td { border-bottom: 1px solid #dde3ea; padding: 2px 5px; vertical-align: top; } p { margin: 2px 0 4px; } .rec { margin: 3px 0 5px; } .rec b { color: #14304f; }
img { width: 100%; } .bx { flex:1; border:1px solid #9fb2c6; padding:3px 3px; border-radius:3px; } .ar { align-self:center; color:#14304f; font-size:8pt; } .small { font-size: 7.8pt; color: #51606f; } code { font-size: 8pt; background: #f1f4f7; padding: 0 2px; }
"""

client = f"""<html><head><meta charset=utf-8><style>{CSS}</style></head><body>
<h1>Fund data controls review: simulated asset manager and insurance book</h1>
<div class=sub>Client note · prepared from the fundlake platform build · period 1 July to 30 September 2026</div>
<div class=tag><b>Illustrative engagement on synthetic data.</b> The funds, securities, brokers, policies and clients are invented, and every fault was planted by the author so that detection could be scored. No real organisation or person is described.</div>
<h2>Summary</h2>
<p>We rebuilt the daily data chain from six source systems, listed and private assets, into one governed platform and tested it against {det['_overall']['planted']} known faults of {n_classes} classes. Every one was found, with no false alarms. On {fd} fund-days, our NAV differed from the administrator's by more than half a basis point on {br}, by up to {mxbps} bp (£{f0(mxgbp)}); every one of the {br} was traced to a named data fault, and {unexpl} were unexplained. Position reconciliation surfaced {eps} separate custodian breaks over {bdays} break-days, of which {explained} are explained by a specific trade the custodian never booked.</p>
<h2>What we found</h2>
<table><tr><th>Finding</th><th>Size</th><th>Why it matters</th></tr>
<tr><td>Missing vendor prices, carried forward</td><td>8 security-days</td><td>The holding is valued at an old price until the vendor catches up; the NAV looks normal.</td></tr>
<tr><td>Stale price (unchanged 6 days) and stale EUR/GBP rate (6 days)</td><td>1 security; 1 pair</td><td>Looks like a valid price. The stale rate moved fund NAVs by up to {stalefx_mx} bp against the administrator.</td></tr>
<tr><td>Replayed OMS batches</td><td>{n_dup} of {n_raw} rows ({n_dup/n_raw:.1%})</td><td>Would double-count trades and create false breaks. Removed before any position is built.</td></tr>
<tr><td>Security that changed ISIN mid-period</td><td>1 security; {naive} custodian rows</td><td>A join on today's identifier loses every row sent under the old one. We join as of the date.</td></tr>
<tr><td>Identifier claimed by two securities</td><td>1 SEDOL</td><td>Earliest claim wins; the later one is ignored and reported to the data owner.</td></tr>
<tr><td>Custodian vs OMS position breaks</td><td>{eps} episodes, {bdays} break-days</td><td>{explained} are one unbooked trade each; 1 position missing at the custodian; 1 unexplained difference that no trade accounts for.</td></tr>
<tr><td>Private markets: a capital-call notice never received</td><td>£{PM['capital_account_breaks']['amount']:,.2f}</td><td>Found only by reconciling the GP's capital account to our notices; until it is obtained, that commitment's TVPI and IRR are wrong, so it is flagged unreconciled.</td></tr>
<tr><td>Private markets: lagged and stale NAVs, a secondary interest</td><td>{PM['statement_lag_days']} days; 3 stale quarters</td><td>Rolling the last statement forward still leaves an average {PM['lag_cost']['mean_abs_pct']}% gap to true value; the secondary shows {PM['secondary']['tvpi_on_purchase_price']:.2f}x on its price against {PM['secondary']['gp_basis_tvpi']:.2f}x on the GP's inherited figures.</td></tr>
<tr><td>Invalid or duplicate insurer claims</td><td>{n_bad} of {n_cl} invalid ({n_bad/n_cl:.1%}); {n_cdup} duplicates</td><td>Orphan claims, losses before inception, and reports before the loss would distort loss ratios.</td></tr></table>
<h2>Fund F03: NAV against the administrator</h2>
<img src="nav_f03.png">
<p class=small>Our NAV minus the administrator NAV, in basis points of administrator NAV. Every excursion beyond tolerance coincides with a flagged carried price, stale price or stale FX day.</p>
<h2>Recommendations</h2>
<div class=rec><b>1. Stop the line before reporting, and say why.</b> Run the quality gate between cleansing and reporting, and publish a NAV only when its carried, stale and stale-FX flags are zero or signed off. Evidence: {br} of {fd} fund-days breached tolerance, all with a flagged cause. Measure: unexplained NAV breaches stay at zero; time to explain a breach falls from a morning's investigation to a lookup.</div>
<div class=rec><b>2. Make identifiers effective-dated, with one owner.</b> Hold SEDOL, ISIN and CUSIP in a cross-reference with validity dates and a rule for conflicts, and resolve every feed through it as of the business date. Evidence: one ISIN change would have dropped {naive} custodian rows under a current-identifier join; one SEDOL was claimed twice. Measure: unresolved-identifier rate held under 0.5% by the gate.</div>
<div class=rec><b>3. Reconcile daily with a cause and an age.</b> Keep the custodian-to-OMS break report as a daily control with a cause hint (the trade that explains the gap, a missing position, or "unexplained") and escalate by age. Evidence: {explained} of {eps} breaks name the unbooked trade; the unexplained one is the case needing the corporate-action feed. Measure: median break age, and share of breaks with a named cause.</div>
<h2>Insurance book and privacy</h2>
<p>Claims pass three validation rules and de-duplication before reaching the marts; the illustrative loss ratios by product range from {min(x[1] for x in lr):.0f}% to {max(x[1] for x in lr):.0f}% (pro-rata earned premium, synthetic). Client names, e-mails, dates of birth and postcodes stay in a separate layer; analysts see a salted client key, an age band and a postcode district, filtered by region by role. The asset listing for the insurance account follows a Solvency II style but is illustrative, not a regulatory template.</p>
<p class=small>Limits: single base currency (GBP), no subscriptions or redemptions, bonds without accrued interest, private-fund cashflows simplified (no recallable distributions or carried-interest waterfall); controls tuned to the fault classes planted here.</p>
</body></html>"""

arch = f"""<html><head><meta charset=utf-8><style>{CSS}</style></head><body>
<h1>fundlake: technical note</h1>
<div class=sub>Governed data platform for a simulated asset manager with an insurance book · v0.1.0 · build {R['build_id']}</div>
<div class=tag><b>Synthetic data throughout.</b> Names are invented; identifiers carry valid check digits but are not issued. Faults are planted by <code>fundlake.synth</code> and recorded in <code>answer_key.json</code>.</div>
<h2>Architecture</h2>
<div style="display:flex;align-items:stretch;gap:3px;font-size:7.4pt;text-align:center;margin:4px 0">
<div class=bx style="background:#eef2f6"><b>Sources</b><br>OMS · custodian · vendor · security master · insurer · GPs</div><div class=ar>&#9654;</div>
<div class=bx style="background:#fff4d6"><b>Contracts</b><br>schema, types, check digits</div><div class=ar>&#9654;</div>
<div class=bx style="background:#e4ecf5"><b>Bronze</b><br>all-varchar + row lineage</div><div class=ar>&#9654;</div>
<div class=bx style="background:#d5e3f1"><b>Silver</b><br>typed, deduplicated, resolved, findings</div><div class=ar>&#9654;</div>
<div class=bx style="background:#f6dcd2;border:1.5px solid #b5542b"><b>Quality gate</b><br>fail = stop, gold unchanged</div><div class=ar>&#9654;</div>
<div class=bx style="background:#c3d8ec"><b>Gold</b><br>star schema, recon, NAV, marts</div><div class=ar>&#9654;</div>
<div class=bx style="background:#e1efe3"><b>Agent / MCP</b><br>audit record</div></div>
<p>Six sources (17 extracts) land as CSV and are checked against YAML data contracts (schema drift, nulls, types, enums, ISIN/SEDOL/CUSIP check digits, volume). dbt builds bronze (all-varchar, with <code>_source_file</code> and <code>_row_number</code>), silver (typed, deduplicated, identifiers resolved as of date, findings) and gold (star schema, reconciliation, NAV control, marts). A rate-based gate sits between silver and gold; a failure stops the run with gold untouched. Each build writes an audit record: input hashes, dbt code hash, versions, row counts, gate results and a hash of the gold layer.</p>
<h2>Two warehouses, one codebase</h2>
<p>Warehouse-specific SQL is confined to eight adapter-dispatched macros in <code>dbt/macros/dialect.sql</code>; everything else is ANSI SQL, with joins instead of correlated subqueries. The DuckDB path runs end to end in tests and under Airflow 3.1 (all five tasks succeed; on corrupted data the gate fails and gold is skipped). The PySpark ingestion step, run locally on Spark 3.5.6, produces all 17 bronze tables identical to DuckDB's. Every model renders for Snowflake and parses as Snowflake SQL; <code>scripts/run_snowflake.py</code> and <code>scripts/run_databricks.sh</code> run the build on those platforms and compare every table, but <b>have not yet been run on a Snowflake or Databricks account</b>.</p>
<h2>Results (reference build)</h2>
<table><tr><th>Check</th><th>Result</th></tr>
<tr><td>Planted faults found / planted; false positives</td><td>{det['_overall']['true_positive']} / {det['_overall']['planted']}; {det['_overall']['false_positive']} ({n_classes} fault classes, each with recall and precision 1.0)</td></tr>
<tr><td>dbt tests (silver / gold)</td><td>silver: {R['dbt']['silver'].get('success',0)} models built, {R['dbt']['silver'].get('pass',0)} tests pass; gold: {R['dbt']['gold'].get('success',0)} models, {R['dbt']['gold'].get('pass',0)} tests pass, {R['dbt']['gold'].get('warn',0)} warn (the warnings are the planted faults surfacing)</td></tr>
<tr><td>Quality gate</td><td>{sum(c['passed'] for c in R['gate'])} of {len(R['gate'])} thresholds pass on the reference data; a corrupted identifier file halts the run at the gate (tested)</td></tr>
<tr><td>NAV control</td><td>{br} of {fd} fund-days beyond 0.5 bp, max {mxbps} bp; {unexpl} unexplained; clean days agree with the administrator to under £1</td></tr>
<tr><td>Private markets</td><td>DPI, RVPI, TVPI and IRR for {PM['commitments']} commitments match an independent Python calculation (IRR within 1e-6); a missing call found by capital-account reconciliation; secondary on purchase price ({PM['secondary']['tvpi_on_purchase_price']:.2f}x) and GP basis ({PM['secondary']['gp_basis_tvpi']:.2f}x)</td></tr>
<tr><td>Reproducibility</td><td>Two builds from the same inputs give the same build id and gold hash (tested)</td></tr>
<tr><td>Agent evaluation (scripted model)</td><td>{ev['n_questions']} questions: accuracy {ev['accuracy_answerable']:.0%} on {ev['answerable']} answerable, abstention recall {ev['abstain_recall']:.0%}, hallucinated numbers {ev['hallucinated_numbers']}</td></tr></table>
<h2>Agentic workflow</h2>
<p>A PydanticAI agent answers control questions through six evidence tools (<code>explain_break</code>, <code>trace_lineage</code>, <code>check_price_staleness</code>, <code>summarise_nav_move</code>, <code>summarise_private_fund</code>, <code>list_findings</code>), also served over MCP. Output is a typed <code>Answer</code>; every number must appear in a tool result (checked), and the agent abstains on unknown funds, dates outside the data, forecasts and questions no tool covers. CI uses a <b>scripted model that is not an LLM</b>: it tests tool wiring, schemas, the faithfulness checker and the evaluation harness, and two deliberately faulty variants (one that invents a figure, one that asserts a cause for an unexplained break) are caught. <b>No real LLM run has been made</b>; <code>scripts/run_agent_live.py</code> does it with an API key.</p>
<h2>Not run, and known limits</h2>
<p>Not yet run: Snowflake and Databricks accounts (runners written and tested offline); any real-LLM evaluation. Limits: detection rules were written knowing the planted fault classes, so recall on unseen fault types is untested; the DuckDB client view enforces row-level security in Python, mirroring but not identical to Snowflake row access policies; single GBP base currency; no accrued interest or subscriptions.</p>
</body></html>"""

out = ROOT / "outputs"
out.mkdir(exist_ok=True)
for name, html in (("fundlake_CLIENT_NOTE", client), ("fundlake_TECHNICAL_NOTE", arch)):
    (ROOT / "docs" / f"{name}.html").write_text(html)
with sync_playwright() as p:
    b = p.chromium.launch()
    for name in ("fundlake_CLIENT_NOTE", "fundlake_TECHNICAL_NOTE"):
        pg = b.new_page()
        pg.goto((ROOT / "docs" / f"{name}.html").as_uri())
        pg.pdf(path=str(out / f"{name}.pdf"), format="A4", print_background=True, margin=dict(top="0", bottom="0", left="0", right="0"))
    b.close()
print("notes built")
