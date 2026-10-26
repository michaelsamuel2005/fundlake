{#- Warehouse-specific SQL lives only here. Everything else in the project is ANSI-compatible and runs unchanged on DuckDB and Snowflake. -#}

{# 1. Bronze read: DuckDB reads the landed CSV directly; Snowflake reads the table loaded by snowflake/load_raw.sql #}
{% macro bronze_select(name) -%} {{ return(adapter.dispatch('bronze_select', 'fundlake')(name)) }} {%- endmacro %}
{% macro default__bronze_select(name) -%}
  select * from {{ source('raw', name) }}
{%- endmacro %}
{% macro duckdb__bronze_select(name) -%}
  select r.*, '{{ name }}.csv' as _source_file, row_number() over () as _row_number
  from read_csv('{{ var("raw_path") }}/{{ name }}.csv', header = true, all_varchar = true) as r
{%- endmacro %}

{# 2. Last non-null value carried forward (price and FX carry-forward) #}
{% macro last_non_null(expr, partition_by, order_by) -%} {{ return(adapter.dispatch('last_non_null', 'fundlake')(expr, partition_by, order_by)) }} {%- endmacro %}
{% macro default__last_non_null(expr, partition_by, order_by) -%}
  last_value({{ expr }} ignore nulls) over (partition by {{ partition_by }} order by {{ order_by }} rows between unbounded preceding and current row)
{%- endmacro %}
{% macro snowflake__last_non_null(expr, partition_by, order_by) -%}
  last_value({{ expr }}) ignore nulls over (partition by {{ partition_by }} order by {{ order_by }} rows between unbounded preceding and current row)
{%- endmacro %}

{# 3. Calendar spine #}
{% macro date_spine_days(start_date, end_date) -%} {{ return(adapter.dispatch('date_spine_days', 'fundlake')(start_date, end_date)) }} {%- endmacro %}
{% macro default__date_spine_days(start_date, end_date) -%}
  select cast(unnest(generate_series(cast('{{ start_date }}' as date), cast('{{ end_date }}' as date), interval 1 day)) as date) as date_day
{%- endmacro %}
{% macro snowflake__date_spine_days(start_date, end_date) -%}
  select dateadd(day, row_number() over (order by seq4()) - 1, cast('{{ start_date }}' as date)) as date_day
  from table(generator(rowcount => 4000))
  qualify date_day <= cast('{{ end_date }}' as date)
{%- endmacro %}

{# 3b. Integer series 0 .. n-1 (grids for the IRR search) #}
{% macro integer_series(n) -%} {{ return(adapter.dispatch('integer_series', 'fundlake')(n)) }} {%- endmacro %}
{% macro default__integer_series(n) -%}
  select cast(unnest(generate_series(0, {{ n }} - 1)) as integer) as i
{%- endmacro %}
{% macro snowflake__integer_series(n) -%}
  select row_number() over (order by seq4()) - 1 as i from table(generator(rowcount => {{ n }}))
{%- endmacro %}

{# 4. String aggregation (lineage lists) #}
{% macro list_agg(expr, sep) -%} {{ return(adapter.dispatch('list_agg', 'fundlake')(expr, sep)) }} {%- endmacro %}
{% macro default__list_agg(expr, sep) -%} string_agg({{ expr }}, '{{ sep }}' order by {{ expr }}) {%- endmacro %}
{% macro snowflake__list_agg(expr, sep) -%} listagg({{ expr }}, '{{ sep }}') within group (order by {{ expr }}) {%- endmacro %}

{# 5. Date arithmetic #}
{% macro days_between(a, b) -%} {{ return(adapter.dispatch('days_between', 'fundlake')(a, b)) }} {%- endmacro %}
{% macro default__days_between(a, b) -%} date_diff('day', {{ a }}, {{ b }}) {%- endmacro %}
{% macro snowflake__days_between(a, b) -%} datediff(day, {{ a }}, {{ b }}) {%- endmacro %}

{% macro years_between(a, b) -%} {{ return(adapter.dispatch('years_between', 'fundlake')(a, b)) }} {%- endmacro %}
{% macro default__years_between(a, b) -%} date_diff('year', {{ a }}, {{ b }}) {%- endmacro %}
{% macro snowflake__years_between(a, b) -%} datediff(year, {{ a }}, {{ b }}) {%- endmacro %}

{# 6. Month truncation to a date #}
{% macro month_start(x) -%} {{ return(adapter.dispatch('month_start', 'fundlake')(x)) }} {%- endmacro %}
{% macro default__month_start(x) -%} cast(date_trunc('month', {{ x }}) as date) {%- endmacro %}
{% macro snowflake__month_start(x) -%} date_trunc('month', {{ x }})::date {%- endmacro %}

{# Portable helpers (no dispatch needed) #}
{% macro to_date(x) -%} cast(try_cast({{ x }} as timestamp) as date) {%- endmacro %}
{% macro hash_key(cols) -%}
  md5(concat_ws('|'{% for c in cols %}, cast({{ c }} as varchar){% endfor %}))
{%- endmacro %}
{% macro src_row(alias='') -%} {{ alias }}_source_file || ':' || cast({{ alias }}_row_number as varchar) {%- endmacro %}
