{#- GP capital-account statement against our records: for each statement, the GP's contributions and distributions to date
    against the notices we hold up to that date (plus, for a secondary interest, what was inherited at the transfer). -#}
with s as (
    select * from {{ ref('silver_pm_nav_statements') }}
), ours as (
    select s.investor_id, s.pm_fund_id, s.period_end,
           coalesce(sum(case when c.flow_type = 'contribution' then c.amount end), 0) as our_contributions,
           coalesce(sum(case when c.flow_type = 'distribution' then c.amount end), 0) as our_distributions
    from s left join {{ ref('fact_pm_cashflow') }} c
      on c.investor_id = s.investor_id and c.pm_fund_id = s.pm_fund_id and c.value_date <= s.period_end and c.flow_type <> 'secondary_purchase'
    group by 1, 2, 3
)
select s.investor_id, s.pm_fund_id, s.period_end,
       s.gp_contributions_to_date, round(o.our_contributions + coalesce(t.inherited_paid_in, 0), 2) as expected_contributions,
       round(s.gp_contributions_to_date - (o.our_contributions + coalesce(t.inherited_paid_in, 0)), 2) + 0 as contributions_diff,
       s.gp_distributions_to_date, round(o.our_distributions + coalesce(t.inherited_distributions, 0), 2) as expected_distributions,
       round(s.gp_distributions_to_date - (o.our_distributions + coalesce(t.inherited_distributions, 0)), 2) + 0 as distributions_diff,
       abs(s.gp_contributions_to_date - (o.our_contributions + coalesce(t.inherited_paid_in, 0))) > 0.01
         or abs(s.gp_distributions_to_date - (o.our_distributions + coalesce(t.inherited_distributions, 0))) > 0.01 as is_break,
       case when s.gp_contributions_to_date - (o.our_contributions + coalesce(t.inherited_paid_in, 0)) > 0.01 then 'call_notice_missing_from_our_feed'
            when s.gp_contributions_to_date - (o.our_contributions + coalesce(t.inherited_paid_in, 0)) < -0.01 then 'call_not_in_gp_statement'
            when s.gp_distributions_to_date - (o.our_distributions + coalesce(t.inherited_distributions, 0)) > 0.01 then 'distribution_notice_missing_from_our_feed'
            when s.gp_distributions_to_date - (o.our_distributions + coalesce(t.inherited_distributions, 0)) < -0.01 then 'distribution_not_in_gp_statement'
       end as cause_hint,
       s.src_row_id
from s
join ours o on o.investor_id = s.investor_id and o.pm_fund_id = s.pm_fund_id and o.period_end = s.period_end
left join {{ ref('silver_pm_transfers') }} t on t.buyer_investor_id = s.investor_id and t.pm_fund_id = s.pm_fund_id
