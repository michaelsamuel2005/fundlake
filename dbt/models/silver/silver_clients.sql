select client_id, client_name, email, {{ to_date('date_of_birth') }} as date_of_birth, postcode
from {{ ref('bronze_insurer_policies') }}
qualify row_number() over (partition by client_id order by policy_id) = 1
