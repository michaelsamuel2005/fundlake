select fund_id, fund_name, base_currency, lower(is_insurance_account) = 'true' as is_insurance_account,
       {{ src_row() }} as src_row_id
from {{ ref('bronze_oms_funds') }}
