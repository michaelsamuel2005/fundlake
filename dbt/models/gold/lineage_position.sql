{#- Gold position -> the trades that built it -> their source rows. -#}
select p.fund_id, p.security_id, p.position_date, count(*) as n_trades,
       {{ list_agg('t.trade_id', ',') }} as trade_ids,
       {{ list_agg('t.src_row_id', ',') }} as trade_src_row_ids
from {{ ref('fact_position') }} p
join {{ ref('fact_trade') }} t on t.fund_id = p.fund_id and t.security_id = p.security_id and t.settle_date <= p.position_date
where p.oms_qty <> 0
group by 1, 2, 3
