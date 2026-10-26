{#- Since-inception IRR per investor and private fund, XIRR convention (dated cashflows, 365-day year, NAV at the reporting
    date as the final inflow). Pure SQL so it runs unchanged on both warehouses: NPV on a grid of rates finds the bracket where
    NPV turns negative, a 1,000-point grid inside that bracket narrows it, and linear interpolation finishes it. -#}
with flows as (
    select investor_id, pm_fund_id, value_date as d, signed_amount as cf from {{ ref('fact_pm_cashflow') }}
    union all
    select investor_id, pm_fund_id, as_of_date as d, nav_asof as cf from {{ ref('pm_nav_asof') }}
), t0 as (
    select investor_id, pm_fund_id, min(d) as d0 from flows group by 1, 2
), f as (
    select f.investor_id, f.pm_fund_id, f.cf, cast({{ days_between('t0.d0', 'f.d') }} as double) / 365.0 as yrs
    from flows f join t0 on t0.investor_id = f.investor_id and t0.pm_fund_id = f.pm_fund_id
), g1 as (
    select -0.95 + 0.01 * i as r from ({{ integer_series(296) }}) s
), npv1 as (
    select f.investor_id, f.pm_fund_id, g1.r, sum(f.cf / power(1 + g1.r, f.yrs)) as npv
    from f cross join g1 group by 1, 2, 3
), b1 as (
    select investor_id, pm_fund_id, r as lo, r + 0.01 as hi
    from (
        select npv1.*, lead(npv) over (partition by investor_id, pm_fund_id order by r) as npv_next
        from npv1
    ) x
    where npv >= 0 and npv_next < 0
    qualify row_number() over (partition by investor_id, pm_fund_id order by r) = 1
), g2 as (
    select i / 1000.0 as u from ({{ integer_series(1001) }}) s
), npv2 as (
    select b1.investor_id, b1.pm_fund_id, b1.lo + (b1.hi - b1.lo) * g2.u as r,
           sum(f.cf / power(1 + b1.lo + (b1.hi - b1.lo) * g2.u, f.yrs)) as npv
    from b1 cross join g2
    join f on f.investor_id = b1.investor_id and f.pm_fund_id = b1.pm_fund_id
    group by 1, 2, 3
), b2 as (
    select investor_id, pm_fund_id, r as lo, npv as npv_lo, r_next as hi, npv_next as npv_hi
    from (
        select npv2.*, lead(npv) over (partition by investor_id, pm_fund_id order by r) as npv_next,
               lead(r) over (partition by investor_id, pm_fund_id order by r) as r_next
        from npv2
    ) x
    where npv >= 0 and npv_next < 0
    qualify row_number() over (partition by investor_id, pm_fund_id order by r) = 1
)
select t0.investor_id, t0.pm_fund_id,
       round(b2.lo + (b2.hi - b2.lo) * b2.npv_lo / (b2.npv_lo - b2.npv_hi), 8) as irr,
       b2.lo is not null as irr_found
from t0 left join b2 on b2.investor_id = t0.investor_id and b2.pm_fund_id = t0.pm_fund_id
