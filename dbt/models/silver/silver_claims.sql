with typed as (
    select claim_id, policy_id, {{ to_date('loss_date') }} as loss_date, {{ to_date('reported_date') }} as reported_date,
           claim_type, try_cast(paid_amount as double) as paid_amount, try_cast(reserve_amount as double) as reserve_amount,
           status, {{ src_row() }} as src_row_id,
           row_number() over (partition by claim_id order by _row_number) as rn,
           count(*) over (partition by claim_id) as n_source_copies
    from {{ ref('bronze_insurer_claims') }}
)
select c.claim_id, c.policy_id, c.loss_date, c.reported_date, c.claim_type, c.paid_amount, c.reserve_amount, c.status,
       c.n_source_copies, c.src_row_id,
       p.policy_id is null as is_orphan,
       coalesce(c.loss_date < p.start_date, false) as is_before_inception,
       c.reported_date < c.loss_date as is_reported_before_loss,
       case when p.policy_id is null then 'orphan_claim'
            when c.loss_date < p.start_date then 'claim_before_inception'
            when c.reported_date < c.loss_date then 'reported_before_loss' end as invalid_reason
from typed c left join {{ ref('silver_policies') }} p on p.policy_id = c.policy_id
where c.rn = 1
