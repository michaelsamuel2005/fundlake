-- Row-level security and column masking for the GDPR-minimised client view on Snowflake (Enterprise edition).
-- scripts/run_snowflake.py applies these and checks them role by role; this file is the reference copy.
-- On DuckDB (which has no row access policies) the same rules are enforced and tested in src/fundlake/privacy.py using
-- contracts/access.yaml. The two are kept in step by hand: that is a known limitation.
use role FUNDLAKE_ROLE; use database FUNDLAKE;
create or replace table SECURITY.REGION_ACCESS (role_name varchar, region varchar);   -- region '*' = all
insert into SECURITY.REGION_ACCESS values
  ('ANALYST_LONDON','London'), ('ANALYST_LONDON','South East'),
  ('ANALYST_NORTH','North West'), ('ANALYST_NORTH','Scotland'),
  ('ANALYST_WALES_MIDS','Wales'), ('ANALYST_WALES_MIDS','Midlands'), ('DATA_STEWARD','*');

create or replace row access policy SECURITY.REGION_POLICY as (row_region varchar) returns boolean ->
  current_role() = 'FUNDLAKE_ROLE' or exists (select 1 from SECURITY.REGION_ACCESS a
          where a.role_name = current_role() and (a.region = '*' or a.region = row_region));
alter table GOLD.CLIENT_POLICY_VIEW add row access policy SECURITY.REGION_POLICY on (region);

-- Pseudonymous key is visible only to stewards
create or replace masking policy SECURITY.CLIENT_KEY_MASK as (v varchar) returns varchar ->
  case when current_role() in ('DATA_STEWARD', 'FUNDLAKE_ROLE') then v else sha2(v) end;
alter table GOLD.CLIENT_POLICY_VIEW modify column client_key set masking policy SECURITY.CLIENT_KEY_MASK;
