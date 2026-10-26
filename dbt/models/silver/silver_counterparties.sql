select counterparty_id, name as counterparty_name, lei, {{ src_row() }} as src_row_id
from {{ ref('bronze_oms_counterparties') }}
