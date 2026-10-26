{#- Quarterly capital-account statements. valuation_change is the NAV move not explained by the GP's own cashflow figures;
    two or more consecutive quarters of exactly zero mean the GP rolled NAV forward without revaluing (stale marks). -#}
with typed as (
    select statement_id, pm_fund_id, investor_id, {{ to_date('period_end') }} as period_end, try_cast(nav as double) as nav,
           try_cast(contributions_to_date as double) as gp_contributions_to_date,
           try_cast(distributions_to_date as double) as gp_distributions_to_date,
           {{ to_date('received_date') }} as received_date, {{ src_row() }} as src_row_id, _row_number
    from {{ ref('bronze_pm_nav_statements') }}
), s as (
    select * from typed
    qualify row_number() over (partition by pm_fund_id, investor_id, period_end order by received_date, _row_number) = 1
), chg as (
    select s.*,
           nav - lag(nav) over (partition by investor_id, pm_fund_id order by period_end)
             - (gp_contributions_to_date - lag(gp_contributions_to_date) over (partition by investor_id, pm_fund_id order by period_end))
             + (gp_distributions_to_date - lag(gp_distributions_to_date) over (partition by investor_id, pm_fund_id order by period_end))
             as valuation_change
    from s
), z as (
    select chg.*, case when valuation_change is not null and abs(valuation_change) < 0.005 then 1 else 0 end as zero_change
    from chg
), runs as (
    select z.*, sum(1 - zero_change) over (partition by investor_id, pm_fund_id order by period_end rows between unbounded preceding and current row) as grp
    from z
)
select statement_id, pm_fund_id, investor_id, period_end, nav, gp_contributions_to_date, gp_distributions_to_date, received_date,
       valuation_change,
       zero_change = 1 and sum(zero_change) over (partition by investor_id, pm_fund_id, grp) >= 2 as is_stale,
       src_row_id
from runs
