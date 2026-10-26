{#- Illustrative: earned premium is pro rata days in force to the as-of date. -#}
with pol as (
    select product, region, annual_premium,
           annual_premium * least(greatest({{ days_between('start_date', "cast('" ~ var('as_of_date') ~ "' as date)") }}, 0), 365) / 365.0 as earned
    from {{ ref('dim_policy') }}
), cl as (select p.product, p.region, sum(c.incurred_amount) as incurred, count(*) as n_claims
          from {{ ref('fact_claim') }} c join {{ ref('dim_policy') }} p on p.policy_id = c.policy_id group by 1, 2)
select pol.product, pol.region, count(*) as n_policies, sum(pol.annual_premium) as written_premium, sum(pol.earned) as earned_premium,
       coalesce(max(cl.incurred), 0) as incurred, coalesce(max(cl.n_claims), 0) as n_claims,
       coalesce(max(cl.incurred), 0) / nullif(sum(pol.earned), 0) as loss_ratio
from pol left join cl on cl.product = pol.product and cl.region = pol.region
group by 1, 2
