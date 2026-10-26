{#- Custodian-to-OMS break report. cause_hint is a rule, not a diagnosis: it names the trade whose quantity explains the gap, if one does.
    Written with joins rather than correlated subqueries so it runs unchanged on DuckDB and Snowflake. -#}
with b as (select * from {{ ref('fact_position') }} where is_break),
bd as (select date_day, row_number() over (order by date_day) as rn from {{ ref('dim_date') }} where is_business_day),
runs as (
    select z.*, sum(case when prev_idx is not null and prev_idx = idx - 1 then 0 else 1 end)
              over (partition by fund_id, security_id order by position_date rows between unbounded preceding and current row) as episode
    from (
        select b.*, bd.rn as idx, lag(bd.rn) over (partition by b.fund_id, b.security_id order by b.position_date) as prev_idx
        from b join bd on bd.date_day = b.position_date
    ) z
), candidates as (
    select r.fund_id, r.security_id, r.position_date, min(t.trade_id) as candidate_trade_id
    from runs r
    join {{ ref('fact_trade') }} t
      on t.fund_id = r.fund_id and t.security_id = r.security_id and t.settle_date <= r.position_date
     and abs(t.signed_qty + r.qty_diff) < {{ var('position_tolerance') }}
    group by 1, 2, 3
)
select r.fund_id, r.security_id, r.position_date, r.oms_qty, r.custodian_qty, r.qty_diff, r.episode,
       case when r.custodian_qty = 0 then 'missing_at_custodian'
            when r.oms_qty = 0 then 'missing_at_oms'
            when c.candidate_trade_id is not null then 'trade_not_reflected_at_custodian'
            else 'unexplained_difference' end as cause_hint,
       c.candidate_trade_id,
       r.custodian_src_row_id
from runs r
left join candidates c on c.fund_id = r.fund_id and c.security_id = r.security_id and c.position_date = r.position_date
