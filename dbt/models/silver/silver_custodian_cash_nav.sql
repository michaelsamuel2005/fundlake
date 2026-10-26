select {{ to_date('position_date') }} as position_date, fund_id,
       try_cast(cash_gbp as double) as cash_gbp, try_cast(admin_nav_gbp as double) as admin_nav_gbp,
       {{ src_row() }} as src_row_id
from {{ ref('bronze_custodian_cash_nav') }}
