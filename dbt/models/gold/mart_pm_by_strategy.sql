select investor_id, strategy, count(*) as n_funds, sum(commitment * fx_rate_to_gbp) as commitment_gbp,
       sum(nav_gbp) as nav_gbp, sum(unfunded_gbp) as unfunded_gbp,
       sum(case when not is_reconciled then 1 else 0 end) as n_unreconciled
from {{ ref('mart_pm_performance') }}
group by 1, 2
