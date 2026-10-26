select pm_fund_id, fund_name, strategy, vintage, currency, fx_pair from {{ ref('silver_pm_funds') }}
