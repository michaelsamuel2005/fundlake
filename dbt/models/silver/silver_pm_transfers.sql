{#- Secondary purchases. Inherited figures are the seller's contributions and distributions to closing, as the GP keeps them. -#}
select transfer_id, pm_fund_id, buyer_investor_id, seller_name,
       {{ to_date('reference_date') }} as reference_date, {{ to_date('transfer_date') }} as transfer_date,
       try_cast(reference_nav as double) as reference_nav, try_cast(purchase_price as double) as purchase_price,
       try_cast(transferred_commitment as double) as transferred_commitment,
       try_cast(inherited_paid_in as double) as inherited_paid_in, try_cast(inherited_distributions as double) as inherited_distributions,
       try_cast(unfunded_at_transfer as double) as unfunded_at_transfer, {{ src_row() }} as src_row_id
from {{ ref('bronze_pm_secondary_transfers') }}
