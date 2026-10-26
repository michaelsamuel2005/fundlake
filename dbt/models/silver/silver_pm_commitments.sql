select commitment_id, investor_id, pm_fund_id, {{ to_date('commitment_date') }} as commitment_date,
       try_cast(commitment_amount as double) as commitment_amount, origin, transfer_id, {{ src_row() }} as src_row_id
from {{ ref('bronze_pm_commitments') }}
