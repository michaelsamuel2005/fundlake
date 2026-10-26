select p.fund_id, p.position_date, p.security_id, p.oms_qty,
       pr.close_price as price, pr.is_carried as price_is_carried, pr.is_stale as price_is_stale, pr.src_row_id as price_src_row_id,
       s.fx_pair, fx.rate as fx_rate, fx.is_carried as fx_is_carried, fx.src_row_id as fx_src_row_id,
       p.oms_qty * pr.close_price / s.price_divisor * fx.rate as mv_gbp
from {{ ref('fact_position') }} p
join {{ ref('dim_security') }} s on s.security_id = p.security_id and p.position_date between s.valid_from and s.valid_to
join {{ ref('fact_price') }} pr on pr.security_id = p.security_id and pr.date_day = p.position_date
join {{ ref('fact_fx') }} fx on fx.pair = s.fx_pair and fx.date_day = p.position_date
where abs(p.oms_qty) > {{ var('position_tolerance') }}
