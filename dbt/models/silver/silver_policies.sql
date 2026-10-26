{#- Personal data is split off into silver_clients; policies carry only the client_id. -#}
select policy_id, client_id, product, {{ to_date('start_date') }} as start_date, {{ to_date('end_date') }} as end_date,
       try_cast(annual_premium as double) as annual_premium, try_cast(sum_insured as double) as sum_insured, region,
       {{ src_row() }} as src_row_id
from {{ ref('bronze_insurer_policies') }}
