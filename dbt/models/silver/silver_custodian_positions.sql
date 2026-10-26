with typed as (
    select {{ to_date('position_date') }} as position_date, fund_id, isin, try_cast(quantity as double) as quantity,
           {{ src_row() }} as src_row_id
    from {{ ref('bronze_custodian_positions') }}
)
select t.position_date, t.fund_id, x.security_id, t.isin, t.quantity, t.src_row_id
from typed t
left join {{ ref('silver_identifiers') }} x
  on x.id_type = 'ISIN' and x.id_value = t.isin and x.is_owner and t.position_date between x.valid_from and x.valid_to
