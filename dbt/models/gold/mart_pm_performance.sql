{#- Private-markets performance per investor and fund, in fund currency, with GBP values at the reporting-date rate.
    For a secondary interest, performance is on the buyer's cost (purchase price plus later calls); the GP-reported basis,
    which inherits the seller's history, is shown alongside. -#}
with cf as (
    select investor_id, pm_fund_id,
           sum(case when flow_type in ('contribution', 'secondary_purchase') then amount else 0 end) as paid_in,
           sum(case when flow_type = 'contribution' then amount else 0 end) as calls,
           sum(case when flow_type = 'distribution' then amount else 0 end) as distributions
    from {{ ref('fact_pm_cashflow') }} group by 1, 2
), rec as (
    select investor_id, pm_fund_id, sum(case when is_break then 1 else 0 end) as n_breaks
    from {{ ref('pm_recon_capital_account') }} group by 1, 2
), flags as (
    select fund_id as investor_id, security_id as pm_fund_id, count(*) as n_findings
    from {{ ref('dq_findings') }} where finding_type like 'pm_%' group by 1, 2
)
select cm.investor_id, cm.pm_fund_id, f.fund_name, f.strategy, f.vintage, f.currency, cm.origin,
       cm.commitment_amount as commitment, cf.paid_in, cf.distributions, n.nav_asof, n.last_statement_date, n.statement_lag_days,
       n.is_rolled_forward,
       cm.commitment_amount - (cf.calls + coalesce(t.inherited_paid_in, 0)) as unfunded,
       cf.distributions / cf.paid_in as dpi,
       n.nav_asof / cf.paid_in as rvpi,
       (cf.distributions + n.nav_asof) / cf.paid_in as tvpi,
       i.irr,
       case when cm.origin = 'secondary'
            then (coalesce(t.inherited_distributions, 0) + cf.distributions + n.nav_asof) / (coalesce(t.inherited_paid_in, 0) + cf.calls) end as gp_basis_tvpi,
       coalesce(rec.n_breaks, 0) = 0 as is_reconciled,
       coalesce(fl.n_findings, 0) as n_findings,
       fx.rate as fx_rate_to_gbp,
       n.nav_asof * fx.rate as nav_gbp,
       (cm.commitment_amount - (cf.calls + coalesce(t.inherited_paid_in, 0))) * fx.rate as unfunded_gbp
from {{ ref('silver_pm_commitments') }} cm
join {{ ref('dim_pm_fund') }} f on f.pm_fund_id = cm.pm_fund_id
join cf on cf.investor_id = cm.investor_id and cf.pm_fund_id = cm.pm_fund_id
join {{ ref('pm_nav_asof') }} n on n.investor_id = cm.investor_id and n.pm_fund_id = cm.pm_fund_id
left join {{ ref('pm_irr') }} i on i.investor_id = cm.investor_id and i.pm_fund_id = cm.pm_fund_id
left join {{ ref('silver_pm_transfers') }} t on t.buyer_investor_id = cm.investor_id and t.pm_fund_id = cm.pm_fund_id
left join rec on rec.investor_id = cm.investor_id and rec.pm_fund_id = cm.pm_fund_id
left join flags fl on fl.investor_id = cm.investor_id and fl.pm_fund_id = cm.pm_fund_id
join {{ ref('fact_fx') }} fx on fx.pair = f.fx_pair and fx.date_day = cast('{{ var("as_of_date") }}' as date)
