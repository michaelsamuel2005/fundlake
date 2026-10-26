{#- Identifier cross-reference. Where one (type, value) is claimed by two securities, the earliest claim owns it and the
    later claim is excluded from resolution (and reported by dq_findings as identifier_conflict). -#}
with typed as (
    select security_id, upper(id_type) as id_type, id_value,
           {{ to_date('valid_from') }} as valid_from,
           coalesce({{ to_date('valid_to') }}, cast('9999-12-31' as date)) as valid_to,
           {{ src_row() }} as src_row_id
    from {{ ref('bronze_secmaster_identifiers') }}
)
select *, row_number() over (partition by id_type, id_value order by valid_from, security_id) = 1 as is_owner
from typed
