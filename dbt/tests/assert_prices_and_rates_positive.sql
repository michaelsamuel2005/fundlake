select 'price' as kind, security_id as k, date_day from {{ ref('fact_price') }} where close_price <= 0
union all select 'fx', pair, date_day from {{ ref('fact_fx') }} where rate <= 0
