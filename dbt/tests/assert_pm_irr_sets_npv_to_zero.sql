-- The IRR must discount the cashflows (and the reporting-date NAV) to zero: |NPV| under one millionth of paid-in.
{{ config(tags=['gold']) }}
with flows as (
    select investor_id, pm_fund_id, value_date as d, signed_amount as cf from {{ ref('fact_pm_cashflow') }}
    union all
    select investor_id, pm_fund_id, as_of_date, nav_asof from {{ ref('pm_nav_asof') }}
), t0 as (select investor_id, pm_fund_id, min(d) as d0 from flows group by 1, 2)
select f.investor_id, f.pm_fund_id, sum(f.cf / power(1 + i.irr, cast({{ days_between('t0.d0', 'f.d') }} as double) / 365.0)) as npv, max(m.paid_in) as paid_in
from flows f
join t0 on t0.investor_id = f.investor_id and t0.pm_fund_id = f.pm_fund_id
join {{ ref('pm_irr') }} i on i.investor_id = f.investor_id and i.pm_fund_id = f.pm_fund_id
join {{ ref('mart_pm_performance') }} m on m.investor_id = f.investor_id and m.pm_fund_id = f.pm_fund_id
group by 1, 2
having abs(sum(f.cf / power(1 + i.irr, cast({{ days_between('t0.d0', 'f.d') }} as double) / 365.0))) > max(m.paid_in) * 0.000001
