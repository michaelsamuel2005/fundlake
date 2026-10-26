{% set rel = ref('client_policy_view') %}
-- Column-level guard: any personal-data column name appearing in the minimised view fails the build.
select column_name from information_schema.columns
where lower(table_schema) = lower('{{ rel.schema }}') and lower(table_name) = lower('{{ rel.identifier }}')
  and lower(column_name) in ('client_name', 'email', 'date_of_birth', 'dob', 'postcode', 'client_id')
