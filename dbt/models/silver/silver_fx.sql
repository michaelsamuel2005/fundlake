select {{ to_date('rate_date') }} as rate_date, pair, try_cast(rate as double) as rate, try_cast(vendor_ts as timestamp) as vendor_ts,
       {{ src_row() }} as src_row_id
from {{ ref('bronze_vendor_fx') }}
