select p.product, p.region, c.accident_month, count(*) as n_claims, sum(c.paid_amount) as paid, sum(c.reserve_amount) as reserve, sum(c.incurred_amount) as incurred
from {{ ref('fact_claim') }} c join {{ ref('dim_policy') }} p on p.policy_id = c.policy_id
group by 1, 2, 3
