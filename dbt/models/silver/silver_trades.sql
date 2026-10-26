{#- One row per trade_id. Replayed feed copies are dropped (earliest received copy kept); SEDOL resolved to security_id as of trade date. -#}
with typed as (
    select trade_id, fund_id, sedol, upper(side) as side,
           try_cast(quantity as double) as quantity, try_cast(price as double) as price, currency,
           {{ to_date('trade_date') }} as trade_date, {{ to_date('settle_date') }} as settle_date,
           counterparty_id, batch_id, try_cast(received_at as timestamp) as received_at,
           {{ src_row() }} as src_row_id
    from {{ ref('bronze_oms_trades') }}
), ranked as (
    select *, row_number() over (partition by trade_id order by received_at, batch_id) as rn,
              count(*) over (partition by trade_id) as n_source_copies
    from typed
)
select r.trade_id, r.fund_id, x.security_id, r.sedol, r.side, r.quantity,
       case when r.side = 'BUY' then r.quantity else -r.quantity end as signed_qty,
       r.price, r.currency, r.trade_date, r.settle_date, r.counterparty_id, r.batch_id, r.received_at,
       r.n_source_copies, r.src_row_id
from ranked r
left join {{ ref('silver_identifiers') }} x
  on x.id_type = 'SEDOL' and x.id_value = r.sedol and x.is_owner and r.trade_date between x.valid_from and x.valid_to
where r.rn = 1
