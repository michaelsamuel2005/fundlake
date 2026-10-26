{#- NAV control: our NAV (OMS quantities x vendor prices x vendor FX + custodian cash) against the administrator NAV. -#}
with comp as (
    select fund_id, position_date, sum(mv_gbp) as securities_mv_gbp, count(*) as n_positions,
           sum(case when price_is_carried then 1 else 0 end) as n_carried_prices,
           sum(case when price_is_stale then 1 else 0 end) as n_stale_prices
    from {{ ref('fact_nav_component') }} group by 1, 2
), fxs as (
    select date_day, max(case when pair = 'EURGBP' then 1 else 0 end) as _e from {{ ref('fact_fx') }} group by 1
), stale_fx as (
    select distinct as_of_date from {{ ref('dq_findings') }} where finding_type = 'stale_fx'
)
select c.fund_id, c.position_date, c.securities_mv_gbp, cash.cash_gbp, c.securities_mv_gbp + cash.cash_gbp as nav_gbp,
       cash.admin_nav_gbp, c.securities_mv_gbp + cash.cash_gbp - cash.admin_nav_gbp as diff_gbp,
       (c.securities_mv_gbp + cash.cash_gbp - cash.admin_nav_gbp) / cash.admin_nav_gbp * 10000 as diff_bps,
       abs((c.securities_mv_gbp + cash.cash_gbp - cash.admin_nav_gbp) / cash.admin_nav_gbp * 10000) > {{ var('nav_tolerance_bps') }} as is_breach,
       c.n_positions, c.n_carried_prices, c.n_stale_prices, sf.as_of_date is not null as stale_fx_day,
       cash.src_row_id as admin_src_row_id
from comp c
join {{ ref('silver_custodian_cash_nav') }} cash on cash.fund_id = c.fund_id and cash.position_date = c.position_date
left join stale_fx sf on sf.as_of_date = c.position_date
