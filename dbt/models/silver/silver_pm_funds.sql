select pm_fund_id, fund_name, strategy, cast(try_cast(vintage as integer) as integer) as vintage, currency,
       case currency when 'EUR' then 'EURGBP' when 'USD' then 'USDGBP' else 'GBPGBP' end as fx_pair,
       {{ src_row() }} as src_row_id
from {{ ref('bronze_pm_funds') }}
