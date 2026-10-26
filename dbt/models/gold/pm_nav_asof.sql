{#- NAV at the reporting date. The latest statement is a quarter old when it arrives, so it is rolled forward with the
    calls and distributions since its period end (the market-standard estimate; valuation changes since are unknown). -#}
with last_statement as (
    select * from {{ ref('silver_pm_nav_statements') }}
    where period_end <= cast('{{ var("as_of_date") }}' as date) and received_date <= cast('{{ var("as_of_date") }}' as date)
    qualify row_number() over (partition by investor_id, pm_fund_id order by period_end desc) = 1
), since as (
    select l.investor_id, l.pm_fund_id,
           coalesce(sum(case when c.flow_type = 'contribution' then c.amount end), 0) as calls_since,
           coalesce(sum(case when c.flow_type = 'distribution' then c.amount end), 0) as distributions_since
    from last_statement l left join {{ ref('fact_pm_cashflow') }} c
      on c.investor_id = l.investor_id and c.pm_fund_id = l.pm_fund_id and c.flow_type <> 'secondary_purchase'
     and c.value_date > l.period_end and c.value_date <= cast('{{ var("as_of_date") }}' as date)
    group by 1, 2
)
select l.investor_id, l.pm_fund_id, l.period_end as last_statement_date, l.nav as last_reported_nav,
       s.calls_since, s.distributions_since, l.nav + s.calls_since - s.distributions_since as nav_asof,
       cast('{{ var("as_of_date") }}' as date) as as_of_date,
       {{ days_between('l.period_end', "cast('" ~ var('as_of_date') ~ "' as date)") }} as statement_lag_days,
       s.calls_since <> 0 or s.distributions_since <> 0 as is_rolled_forward, l.src_row_id
from last_statement l join since s on s.investor_id = l.investor_id and s.pm_fund_id = l.pm_fund_id
