{#- One row per notice line; a resent notice resends its lines, so lines are deduplicated on (notice_id, line_no). -#}
with typed as (
    select notice_id, cast(try_cast(line_no as integer) as integer) as line_no, component, try_cast(amount as double) as amount,
           {{ src_row() }} as src_row_id,
           row_number() over (partition by notice_id, line_no order by _row_number) as rn
    from {{ ref('bronze_pm_notice_lines') }}
)
select notice_id, line_no, component, amount, src_row_id from typed where rn = 1
