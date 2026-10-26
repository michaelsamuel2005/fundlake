{#- Every business day x pair; a missing vendor rate is carried forward and flagged. GBPGBP = 1. -#}
with pairs as (select 'EURGBP' as pair union all select 'USDGBP' union all select 'GBPGBP'),
grid as (select d.date_day, p.pair from {{ ref('dim_date') }} d cross join pairs p where d.is_business_day),
j as (
    select g.date_day, g.pair, case when g.pair = 'GBPGBP' then 1.0 else f.rate end as rate_raw, f.src_row_id
    from grid g left join {{ ref('silver_fx') }} f on f.pair = g.pair and f.rate_date = g.date_day
)
select date_day, pair,
       {{ last_non_null('rate_raw', 'pair', 'date_day') }} as rate,
       rate_raw is null as is_carried,
       {{ last_non_null('src_row_id', 'pair', 'date_day') }} as src_row_id
from j
