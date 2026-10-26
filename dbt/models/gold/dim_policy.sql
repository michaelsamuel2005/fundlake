select policy_id, {{ hash_key(["'" ~ var('pii_salt') ~ "'", 'client_id']) }} as client_key, product, region, start_date, end_date, annual_premium, sum_insured
from {{ ref('silver_policies') }}
