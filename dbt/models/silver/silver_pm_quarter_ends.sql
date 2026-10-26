{#- Calendar quarter ends, for the expected-statements check. Portable: no date functions beyond extract. -#}
with d as ({{ date_spine_days(var('pm_first_quarter'), '2026-12-31') }})
select date_day as period_end from d
where (extract(month from date_day) = 3 and extract(day from date_day) = 31)
   or (extract(month from date_day) = 6 and extract(day from date_day) = 30)
   or (extract(month from date_day) = 9 and extract(day from date_day) = 30)
   or (extract(month from date_day) = 12 and extract(day from date_day) = 31)
