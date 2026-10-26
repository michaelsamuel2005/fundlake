{#- Quarterly NAV per investor and private fund. A statement that never arrived is estimated by rolling the previous
    reported NAV forward with the cashflows in between (nav_source = 'rolled_forward'); stale marks are flagged. -#}
with first_pe as (
    select investor_id, pm_fund_id, min(period_end) as first_period_end, max(period_end) as last_period_end
    from {{ ref('silver_pm_nav_statements') }} group by 1, 2
), grid as (
    select f.investor_id, f.pm_fund_id, q.period_end
    from first_pe f join {{ ref('silver_pm_quarter_ends') }} q on q.period_end between f.first_period_end and f.last_period_end
), j as (
    select g.investor_id, g.pm_fund_id, g.period_end, s.nav as reported_nav, s.is_stale, s.src_row_id,
           {{ last_non_null('s.nav', 'g.investor_id, g.pm_fund_id', 'g.period_end') }} as last_reported_nav,
           {{ last_non_null('s.period_end', 'g.investor_id, g.pm_fund_id', 'g.period_end') }} as last_reported_period_end
    from grid g
    left join {{ ref('silver_pm_nav_statements') }} s on s.investor_id = g.investor_id and s.pm_fund_id = g.pm_fund_id and s.period_end = g.period_end
), net as (
    select j.investor_id, j.pm_fund_id, j.period_end,
           coalesce(sum(case when c.flow_type = 'contribution' then c.amount when c.flow_type = 'distribution' then -c.amount else 0 end), 0) as net_flows_since
    from j left join {{ ref('fact_pm_cashflow') }} c
      on c.investor_id = j.investor_id and c.pm_fund_id = j.pm_fund_id
     and c.value_date > j.last_reported_period_end and c.value_date <= j.period_end
    group by 1, 2, 3
), est as (
    select j.*, n.net_flows_since
    from j join net n on n.investor_id = j.investor_id and n.pm_fund_id = j.pm_fund_id and n.period_end = j.period_end
)
select investor_id, pm_fund_id, period_end,
       coalesce(reported_nav, last_reported_nav + net_flows_since) as nav,
       case when reported_nav is null then 'rolled_forward' else 'reported' end as nav_source,
       coalesce(is_stale, false) as is_stale, src_row_id
from est
