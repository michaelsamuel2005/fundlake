{#- Type 2: one row per security per attribute version. Facts join as of the event date. -#}
select security_sk, security_id, name, asset_class, currency, country, sector, rating, price_basis, settle_lag, fx_pair,
       valid_from, valid_to, is_current,
       case price_basis when 'per100' then 100.0 else 1.0 end as price_divisor
from {{ ref('silver_securities') }}
