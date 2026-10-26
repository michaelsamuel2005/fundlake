select fund_id, fund_name, base_currency, is_insurance_account from {{ ref('silver_funds') }}
