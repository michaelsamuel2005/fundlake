-- A NAV breach with no flagged data fault behind it is a control failure: warn (never silently pass).
{{ config(severity='warn', tags=['control']) }}
select fund_id, position_date, diff_bps from {{ ref('fact_nav') }}
where is_breach and n_carried_prices = 0 and n_stale_prices = 0 and not stale_fx_day
