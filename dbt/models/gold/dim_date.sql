with spine as ({{ date_spine_days('2026-06-29', '2026-10-02') }}),
bd as (select distinct position_date as d from {{ ref('silver_custodian_cash_nav') }})
select s.date_day as date_day, bd.d is not null as is_business_day,
       extract(year from s.date_day) as year, extract(month from s.date_day) as month
from spine s left join bd on bd.d = s.date_day
