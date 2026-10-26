select t.trade_id, t.fund_id, t.security_id, s.security_sk, t.counterparty_id, t.side, t.quantity, t.signed_qty, t.price, t.currency,
       t.trade_date, t.settle_date,
       t.quantity * t.price / s.price_divisor * fx.rate as notional_gbp,
       t.n_source_copies, t.src_row_id
from {{ ref('silver_trades') }} t
join {{ ref('dim_security') }} s on s.security_id = t.security_id and t.trade_date between s.valid_from and s.valid_to
join {{ ref('fact_fx') }} fx on fx.pair = s.fx_pair and fx.date_day = t.trade_date
