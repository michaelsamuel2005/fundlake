select counterparty_id, counterparty_name, lei from {{ ref('silver_counterparties') }}
