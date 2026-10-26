select claim_id, policy_id, loss_date, reported_date, {{ month_start('loss_date') }} as accident_month, claim_type, paid_amount, reserve_amount,
       paid_amount + reserve_amount as incurred_amount, status, src_row_id
from {{ ref('silver_claims') }} where invalid_reason is null
