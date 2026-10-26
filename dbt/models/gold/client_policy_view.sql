{#- GDPR-minimised view: no name, e-mail, date of birth or full postcode. Pseudonymous client_key, age band, postcode district.
    Row-level security by region is enforced by snowflake/rls.sql on Snowflake and by fundlake.privacy.client_view on DuckDB. -#}
select p.policy_id, p.client_key, pol.region, pol.product,
       case when {{ years_between('c.date_of_birth', "cast('" ~ var('as_of_date') ~ "' as date)") }} < 40 then 'under 40'
            when {{ years_between('c.date_of_birth', "cast('" ~ var('as_of_date') ~ "' as date)") }} < 60 then '40-59' else '60+' end as age_band,
       split_part(c.postcode, ' ', 1) as postcode_district,
       case when p.annual_premium < 500 then 'under 500' when p.annual_premium < 1000 then '500-999' else '1000+' end as premium_band
from {{ ref('dim_policy') }} p
join {{ ref('silver_policies') }} pol on pol.policy_id = p.policy_id
join {{ ref('silver_clients') }} c on c.client_id = pol.client_id
