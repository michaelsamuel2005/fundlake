select fund_id, security_id, position_date, count(*) from {{ ref('fact_position') }} group by 1, 2, 3 having count(*) > 1
