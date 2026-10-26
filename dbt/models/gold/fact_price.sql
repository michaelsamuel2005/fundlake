{#- Every security x business day. A missing close is carried forward and flagged; stale closes are flagged from dq_findings. -#}
with grid as (select s.security_id, d.date_day from (select distinct security_id from {{ ref('dim_security') }}) s
              cross join {{ ref('dim_date') }} d where d.is_business_day),
j as (select g.security_id, g.date_day, p.close_price, p.src_row_id from grid g
      left join {{ ref('silver_prices') }} p on p.security_id = g.security_id and p.price_date = g.date_day),
c as (
    select security_id, date_day,
           {{ last_non_null('close_price', 'security_id', 'date_day') }} as close_price,
           close_price is null as is_carried,
           {{ last_non_null('src_row_id', 'security_id', 'date_day') }} as src_row_id
    from j
)
select c.security_id, c.date_day, c.close_price, c.is_carried,
       coalesce(sp.entity_key is not null, false) as is_stale, c.src_row_id
from c left join (select distinct security_id, as_of_date, entity_key from {{ ref('dq_findings') }} where finding_type = 'stale_price') sp
  on sp.security_id = c.security_id and sp.as_of_date = c.date_day
