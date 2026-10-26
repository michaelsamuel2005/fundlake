-- TVPI = DPI + RVPI, one row per commitment, and a secondary's paid-in is its purchase price plus later calls.
select m.investor_id, m.pm_fund_id, 'tvpi' as check_name from {{ ref('mart_pm_performance') }} m where abs(m.tvpi - (m.dpi + m.rvpi)) > 1e-9
union all
select investor_id, pm_fund_id, 'duplicate' from {{ ref('mart_pm_performance') }} group by 1, 2 having count(*) > 1
union all
select m.investor_id, m.pm_fund_id, 'secondary_basis'
from {{ ref('mart_pm_performance') }} m
join {{ ref('silver_pm_transfers') }} t on t.buyer_investor_id = m.investor_id and t.pm_fund_id = m.pm_fund_id
join (select investor_id, pm_fund_id, sum(amount) as calls from {{ ref('fact_pm_cashflow') }} where flow_type = 'contribution' group by 1, 2) c
  on c.investor_id = m.investor_id and c.pm_fund_id = m.pm_fund_id
where abs(m.paid_in - (t.purchase_price + c.calls)) > 0.01
