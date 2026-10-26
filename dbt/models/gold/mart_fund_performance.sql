select fund_id, position_date, nav_gbp,
       nav_gbp / lag(nav_gbp) over (partition by fund_id order by position_date) - 1 as daily_return,
       nav_gbp / first_value(nav_gbp) over (partition by fund_id order by position_date) - 1 as cumulative_return
from {{ ref('fact_nav') }}
