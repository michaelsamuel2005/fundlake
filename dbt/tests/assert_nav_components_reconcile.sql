-- Gold NAV must equal the sum of its components plus cash (no value created or lost between layers).
select n.fund_id, n.position_date, n.securities_mv_gbp, c.s
from {{ ref('fact_nav') }} n
join (select fund_id, position_date, sum(mv_gbp) as s from {{ ref('fact_nav_component') }} group by 1, 2) c
  on c.fund_id = n.fund_id and c.position_date = n.position_date
where abs(n.securities_mv_gbp - c.s) > 0.01
   or abs(n.nav_gbp - (c.s + n.cash_gbp)) > 0.01
