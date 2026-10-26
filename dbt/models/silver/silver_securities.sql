{#- Type-2 history of the security master. valid_to is inclusive; the open record carries 9999-12-31. -#}
with typed as (
    select security_id, name, asset_class, currency, country, sector, rating, price_basis,
           cast(try_cast(settle_lag as integer) as integer) as settle_lag,
           {{ to_date('valid_from') }} as vf, {{ to_date('valid_to') }} as vt,
           {{ src_row() }} as src_row_id
    from {{ ref('bronze_secmaster_securities') }}
)
select security_id, name, asset_class, currency, country, sector, rating, price_basis, settle_lag,
       vf as valid_from, coalesce(vt, cast('9999-12-31' as date)) as valid_to, vt is null as is_current,
       {{ hash_key(['security_id', 'vf']) }} as security_sk,
       case currency when 'EUR' then 'EURGBP' when 'USD' then 'USDGBP' else 'GBPGBP' end as fx_pair,
       src_row_id
from typed
