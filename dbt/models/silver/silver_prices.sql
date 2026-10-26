with typed as (
    select {{ to_date('price_date') }} as price_date, isin, try_cast(close_price as double) as close_price, currency,
           try_cast(vendor_ts as timestamp) as vendor_ts, {{ src_row() }} as src_row_id
    from {{ ref('bronze_vendor_prices') }}
), res as (
    select t.*, x.security_id
    from typed t
    left join {{ ref('silver_identifiers') }} x
      on x.id_type = 'ISIN' and x.id_value = t.isin and x.is_owner and t.price_date between x.valid_from and x.valid_to
)
select * from res
qualify row_number() over (partition by security_id, price_date order by vendor_ts desc) = 1
