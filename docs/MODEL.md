# Data model

All data is synthetic. Layers: **bronze** (landed, all-varchar, with `_source_file` and `_row_number`), **silver** (typed, deduplicated, identifiers resolved, findings), **gold** (star schema, controls, marts).

## Flow

```mermaid
flowchart TB
  subgraph Sources
    OMS[OMS trades]; CUS[Custodian positions, cash, NAV]; VEN[Vendor prices and FX]; SEC[Security master and identifiers]; INS[Insurer policies and claims]
  end
  Sources --> CON{{Data contracts}}
  CON --> BRZ[Bronze]
  BRZ --> SLV[Silver: typed, deduplicated, resolved, dq_findings]
  SLV --> GATE{Quality gate}
  GATE -- pass --> GLD[Gold: star schema, recon, NAV check, marts]
  GATE -- fail --> STOP([Pipeline halts; gold unchanged])
  GLD --> AGT[Evidence tools: PydanticAI agent and MCP server]
  GLD --> AUD[(Audit record: inputs, code, outputs, hash)]
```

## Star schema: holdings and performance

```mermaid
erDiagram
  dim_fund ||--o{ fact_trade : fund_id
  dim_security ||--o{ fact_trade : security_sk
  dim_counterparty ||--o{ fact_trade : counterparty_id
  dim_fund ||--o{ fact_position : fund_id
  dim_security ||--o{ fact_position : "security_id as of date"
  dim_date ||--o{ fact_position : position_date
  fact_position ||--o{ fact_nav_component : "fund, security, date"
  fact_price ||--o{ fact_nav_component : "security, date"
  fact_fx ||--o{ fact_nav_component : "pair, date"
  fact_nav_component }o--|| fact_nav : "fund, date"
  fact_position ||--o{ recon_position_breaks : is_break
```

## Insurance mart

```mermaid
erDiagram
  dim_policy ||--o{ fact_claim : policy_id
  dim_policy ||--|| client_policy_view : "client_key (minimised)"
  fact_claim }o--|| mart_claims_summary : "product, region, month"
  dim_policy }o--|| mart_loss_ratio : "product, region"
```

## Design decisions

- **Type 2 on the security master.** `dim_security` keeps one row per attribute version (`valid_from`, `valid_to` inclusive). Facts join as of the event date, so a bond downgraded on 20 August is valued and listed with the rating in force on the date being reported.
- **Identifiers are effective-dated and have an owner.** The cross-reference maps SEDOL (OMS), ISIN (custodian, vendor) and CUSIP to `security_id` with validity dates. If two securities claim one identifier, the earliest claim owns it and the later one is ignored and reported (`identifier_conflict`).
- **Missing data is filled visibly.** A missing vendor price or FX rate is carried forward and flagged (`is_carried`); a price unchanged for four or more days is flagged stale. NAV rows count both.
- **Detection and prevention are separate.** `dq_findings` reports; the gate (`contracts/gate.yaml`) decides, by rate, whether the run may continue.
- **Personal data is split at silver.** `silver_clients` holds names, e-mails, dates of birth and postcodes; gold carries a salted pseudonymous key, an age band and a postcode district only.
- **Lineage is by construction.** Every silver and gold row carries `src_row_id` (`file:row`) or a list of them; `fundlake.lineage` re-reads the raw row to prove it.

## Simplifications (deliberate)

All funds are GBP-based; bonds are quoted per 100 par without accrued interest; there are no external subscriptions or redemptions, so returns are NAV changes; the Solvency II-style listing is illustrative and not an EIOPA template; earned premium is pro rata days in force.
