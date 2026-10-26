select c.claim_id from {{ ref('fact_claim') }} c left join {{ ref('dim_policy') }} p on p.policy_id = c.policy_id where p.policy_id is null
