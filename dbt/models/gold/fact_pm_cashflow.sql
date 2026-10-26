{#- LP cashflows by investor and private fund, from the investor's side: contributions and the secondary purchase price are
    outflows, distributions inflows. For a secondary interest only cashflows after the transfer are the buyer's. -#}
with notices as (
    select investor_id, pm_fund_id, value_date,
           case notice_type when 'capital_call' then 'contribution' else 'distribution' end as flow_type,
           amount, investment, fees, income, return_of_capital, realised_gain,
           notice_id as source_id, is_sign_corrected, is_lines_mismatch, src_row_id
    from {{ ref('silver_pm_notices') }}
    where not is_unknown_investor
), purchases as (
    select buyer_investor_id as investor_id, pm_fund_id, transfer_date as value_date, 'secondary_purchase' as flow_type,
           purchase_price as amount, cast(0 as double) as investment, cast(0 as double) as fees, cast(0 as double) as income,
           cast(0 as double) as return_of_capital, cast(0 as double) as realised_gain,
           transfer_id as source_id, false as is_sign_corrected, false as is_lines_mismatch, src_row_id
    from {{ ref('silver_pm_transfers') }}
), u as (
    select * from notices union all select * from purchases
)
select u.*, case when flow_type = 'distribution' then amount else -amount end as signed_amount
from u
