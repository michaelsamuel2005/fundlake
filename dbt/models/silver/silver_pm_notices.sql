{#- One row per notice_id (resent copies dropped). The amount used downstream is the sum of the line items:
    a header that disagrees with its lines is flagged; a negative header whose positive lines sum to its absolute value is a
    sign keying error, corrected and flagged. Notices addressed to an investor with no commitment are flagged and excluded. -#}
with typed as (
    select notice_id, pm_fund_id, investor_id, notice_type, {{ to_date('notice_date') }} as notice_date,
           {{ to_date('value_date') }} as value_date, currency, try_cast(total_amount as double) as header_amount,
           try_cast(received_at as timestamp) as received_at, {{ src_row() }} as src_row_id,
           row_number() over (partition by notice_id order by try_cast(received_at as timestamp), _row_number) as rn,
           count(*) over (partition by notice_id) as n_source_copies
    from {{ ref('bronze_pm_notices') }}
), lines as (
    select notice_id, sum(amount) as lines_total, count(*) as n_lines, min(amount) as min_line,
           sum(case when component = 'investment' then amount else 0 end) as investment,
           sum(case when component in ('management_fee', 'fund_expenses') then amount else 0 end) as fees,
           sum(case when component = 'income' then amount else 0 end) as income,
           sum(case when component = 'return_of_capital' then amount else 0 end) as return_of_capital,
           sum(case when component = 'realised_gain' then amount else 0 end) as realised_gain
    from {{ ref('silver_pm_notice_lines') }} group by 1
)
select n.notice_id, n.pm_fund_id, n.investor_id, n.notice_type, n.notice_date, n.value_date, n.currency, n.header_amount,
       round(l.lines_total, 2) as amount, l.n_lines, l.investment, l.fees, l.income, l.return_of_capital, l.realised_gain,
       n.header_amount < 0 and l.min_line >= 0 and abs(abs(n.header_amount) - l.lines_total) < 0.005 as is_sign_corrected,
       abs(abs(n.header_amount) - l.lines_total) >= 0.005 as is_lines_mismatch,
       c.commitment_id is null as is_unknown_investor,
       n.n_source_copies, n.received_at, n.src_row_id
from typed n
left join lines l on l.notice_id = n.notice_id
left join {{ ref('silver_pm_commitments') }} c on c.investor_id = n.investor_id and c.pm_fund_id = n.pm_fund_id
where n.rn = 1
