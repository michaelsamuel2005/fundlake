{#- ILLUSTRATIVE Solvency II-style asset listing for the insurance general account on the latest date. NOT an EIOPA S.06.02 template. -#}
with latest as (select max(position_date) as d from {{ ref('fact_nav') }})
select n.security_id as asset_id, s.name, case s.asset_class when 'equity' then 'Listed equity (CIC-style 3x)' else 'Corporate bond (CIC-style 2x)' end as asset_category,
       s.currency, n.oms_qty as quantity, n.price, n.mv_gbp as market_value_gbp,
       n.mv_gbp / sum(n.mv_gbp) over () as weight, s.rating,
       case s.rating when 'AAA' then 0 when 'AA' then 1 when 'A' then 2 when 'BBB' then 3 when 'BB' then 4 when 'B' then 5 end as credit_quality_step,
       'illustrative, synthetic data' as disclaimer
from {{ ref('fact_nav_component') }} n
join {{ ref('dim_security') }} s on s.security_id = n.security_id and n.position_date between s.valid_from and s.valid_to
join {{ ref('dim_fund') }} f on f.fund_id = n.fund_id and f.is_insurance_account
where n.position_date = (select d from latest)
