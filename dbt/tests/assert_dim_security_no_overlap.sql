-- Type-2 versions of one security must not overlap in time.
select a.security_id, a.valid_from, b.valid_from as other_from
from {{ ref('dim_security') }} a join {{ ref('dim_security') }} b
  on a.security_id = b.security_id and a.security_sk <> b.security_sk and a.valid_from <= b.valid_to and b.valid_from <= a.valid_to
