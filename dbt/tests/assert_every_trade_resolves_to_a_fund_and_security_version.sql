select t.trade_id from {{ ref('silver_trades') }} t left join {{ ref('fact_trade') }} f on f.trade_id = t.trade_id where f.trade_id is null
