{#- Source-level findings. One row per affected entity. Detection only: nothing here deletes data; silver models already
    resolved or flagged what they could. Gold adds position breaks and NAV exceptions. -#}
with dup_trades as (
    select 'duplicate_trade' as finding_type, trade_id as entity_key, cast(null as varchar) as security_id,
           fund_id, trade_date as as_of_date, 'trade_id seen ' || cast(n_source_copies as varchar) || ' times in the OMS feed; first copy kept' as detail
    from {{ ref('silver_trades') }} where n_source_copies > 1
), id_change as (
    select 'identifier_change', security_id, security_id, cast(null as varchar), min(valid_from),
           'ISIN changed: ' || {{ list_agg('id_value', ' -> ') }}
    from {{ ref('silver_identifiers') }} where id_type = 'ISIN'
    group by security_id having count(*) > 1
), id_conflict as (
    select 'identifier_conflict', id_type || ':' || id_value, security_id, cast(null as varchar), valid_from,
           id_type || ' ' || id_value || ' is also owned by an earlier record; this claim ignored'
    from {{ ref('silver_identifiers') }} where not is_owner
), held as (
    select distinct security_id, position_date from {{ ref('silver_custodian_positions') }} where security_id is not null and quantity > 0
), miss_px as (
    select 'missing_price', h.security_id || ':' || cast(h.position_date as varchar), h.security_id, cast(null as varchar), h.position_date,
           'held security has no vendor price on a business day; last price carried forward'
    from held h left join {{ ref('silver_prices') }} p on p.security_id = h.security_id and p.price_date = h.position_date
    where p.security_id is null
), px_runs as (
    select security_id, price_date, close_price,
           sum(chg) over (partition by security_id order by price_date rows between unbounded preceding and current row) as grp
    from (select *, case when close_price = lag(close_price) over (partition by security_id order by price_date) then 0 else 1 end as chg
          from {{ ref('silver_prices') }} where security_id is not null) z
), stale_px as (
    select 'stale_price', security_id || ':' || cast(price_date as varchar), security_id, cast(null as varchar), price_date,
           'unchanged close for ' || cast(n as varchar) || ' consecutive days'
    from (select *, count(*) over (partition by security_id, grp) as n from px_runs) q where n >= {{ var('stale_run_length') }}
), fx_runs as (
    select pair, rate_date, rate,
           sum(chg) over (partition by pair order by rate_date rows between unbounded preceding and current row) as grp
    from (select *, case when rate = lag(rate) over (partition by pair order by rate_date) then 0 else 1 end as chg
          from {{ ref('silver_fx') }}) z
), stale_fx as (
    select 'stale_fx', pair || ':' || cast(rate_date as varchar), cast(null as varchar), cast(null as varchar), rate_date,
           pair || ' unchanged for ' || cast(n as varchar) || ' consecutive days'
    from (select *, count(*) over (partition by pair, grp) as n from fx_runs) q where n >= {{ var('stale_run_length') }}
), claim_issues as (
    select coalesce(invalid_reason, 'duplicate_claim'), claim_id, cast(null as varchar), cast(null as varchar), loss_date,
           'claim ' || claim_id || ' failed validation'
    from {{ ref('silver_claims') }} where invalid_reason is not null
    union all
    select 'duplicate_claim', claim_id, cast(null as varchar), cast(null as varchar), loss_date, 'claim_id seen ' || cast(n_source_copies as varchar) || ' times'
    from {{ ref('silver_claims') }} where n_source_copies > 1
), pm_notice_issues as (
    select 'pm_duplicate_notice', notice_id, pm_fund_id, investor_id, value_date,
           'notice delivered ' || cast(n_source_copies as varchar) || ' times; first copy kept'
    from {{ ref('silver_pm_notices') }} where n_source_copies > 1
    union all
    select 'pm_notice_lines_mismatch', notice_id, pm_fund_id, investor_id, value_date,
           'header ' || cast(header_amount as varchar) || ' disagrees with lines ' || cast(amount as varchar) || '; lines used, confirm with the GP'
    from {{ ref('silver_pm_notices') }} where is_lines_mismatch
    union all
    select 'pm_sign_corrected', notice_id, pm_fund_id, investor_id, value_date,
           'header keyed negative; positive lines sum to its absolute value; sign corrected'
    from {{ ref('silver_pm_notices') }} where is_sign_corrected
    union all
    select 'pm_unknown_investor', notice_id, pm_fund_id, investor_id, value_date,
           'addressed to ' || investor_id || ', who has no commitment in ' || pm_fund_id || '; excluded'
    from {{ ref('silver_pm_notices') }} where is_unknown_investor
), pm_cum as (
    select n.investor_id, n.pm_fund_id, n.value_date, cm.commitment_amount,
           sum(n.amount) over (partition by n.investor_id, n.pm_fund_id order by n.value_date, n.notice_id rows between unbounded preceding and current row)
             + coalesce(t.inherited_paid_in, 0) as cum_called
    from {{ ref('silver_pm_notices') }} n
    join {{ ref('silver_pm_commitments') }} cm on cm.investor_id = n.investor_id and cm.pm_fund_id = n.pm_fund_id
    left join {{ ref('silver_pm_transfers') }} t on t.buyer_investor_id = n.investor_id and t.pm_fund_id = n.pm_fund_id
    where n.notice_type = 'capital_call' and not n.is_unknown_investor
), pm_over as (
    select 'pm_over_call', investor_id || ':' || pm_fund_id, pm_fund_id, investor_id, min(value_date),
           'cumulative calls exceed the commitment of ' || cast(max(commitment_amount) as varchar)
    from pm_cum where cum_called > commitment_amount + 0.005 group by investor_id, pm_fund_id
), pm_expected_statements as (
    select b.investor_id, b.pm_fund_id, q.period_end
    from (select investor_id, pm_fund_id, min(period_end) as first_period_end from {{ ref('silver_pm_nav_statements') }} group by 1, 2) b
    join {{ ref('silver_pm_quarter_ends') }} q
      on q.period_end >= b.first_period_end
     and {{ days_between('q.period_end', "cast('" ~ var('as_of_date') ~ "' as date)") }} >= {{ var('pm_statement_due_days') }}
), pm_statement_issues as (
    select 'pm_missing_nav_statement', e.investor_id || ':' || e.pm_fund_id || ':' || cast(e.period_end as varchar), e.pm_fund_id, e.investor_id, e.period_end,
           'statement due {{ var("pm_statement_due_days") }} days after quarter end was not delivered; NAV rolled forward for this quarter'
    from pm_expected_statements e
    left join {{ ref('silver_pm_nav_statements') }} s on s.investor_id = e.investor_id and s.pm_fund_id = e.pm_fund_id and s.period_end = e.period_end
    where s.statement_id is null
    union all
    select 'pm_stale_nav', investor_id || ':' || pm_fund_id || ':' || cast(period_end as varchar), pm_fund_id, investor_id, period_end,
           'NAV moved only by cashflows for two or more quarters running: no revaluation'
    from {{ ref('silver_pm_nav_statements') }} where is_stale
)
select finding_type, entity_key, security_id, fund_id, as_of_date, detail, 'warn' as severity from (
    select * from dup_trades union all select * from id_change union all select * from id_conflict
    union all select * from miss_px union all select * from stale_px union all select * from stale_fx union all select * from claim_issues
    union all select * from pm_notice_issues union all select * from pm_over union all select * from pm_statement_issues
) f
