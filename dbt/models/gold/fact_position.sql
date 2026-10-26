{#- OMS-derived settled position vs custodian position, per fund x security x business day. -#}
with delta as (select fund_id, security_id, settle_date as position_date, sum(signed_qty) as delta from {{ ref('fact_trade') }} group by 1, 2, 3),
bounds as (select fund_id, security_id, min(position_date) as first_date from delta group by 1, 2),
grid as (select b.fund_id, b.security_id, d.date_day as position_date from bounds b
         join {{ ref('dim_date') }} d on d.is_business_day and d.date_day >= b.first_date),
oms as (
    select g.fund_id, g.security_id, g.position_date,
           sum(coalesce(x.delta, 0)) over (partition by g.fund_id, g.security_id order by g.position_date rows between unbounded preceding and current row) as oms_qty
    from grid g left join delta x on x.fund_id = g.fund_id and x.security_id = g.security_id and x.position_date = g.position_date
), cust as (
    select fund_id, security_id, position_date, quantity as custodian_qty, src_row_id as custodian_src_row_id
    from {{ ref('silver_custodian_positions') }}
)
select coalesce(o.fund_id, c.fund_id) as fund_id, coalesce(o.security_id, c.security_id) as security_id,
       coalesce(o.position_date, c.position_date) as position_date,
       coalesce(o.oms_qty, 0) as oms_qty, coalesce(c.custodian_qty, 0) as custodian_qty,
       coalesce(c.custodian_qty, 0) - coalesce(o.oms_qty, 0) as qty_diff,
       abs(coalesce(c.custodian_qty, 0) - coalesce(o.oms_qty, 0)) > {{ var('position_tolerance') }} as is_break,
       c.custodian_src_row_id
from oms o full outer join cust c
  on o.fund_id = c.fund_id and o.security_id = c.security_id and o.position_date = c.position_date
where abs(coalesce(o.oms_qty, 0)) > {{ var('position_tolerance') }} or abs(coalesce(c.custodian_qty, 0)) > {{ var('position_tolerance') }}
