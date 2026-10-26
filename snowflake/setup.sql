-- Superseded by `python scripts/run_snowflake.py keygen`, which prints this setup with a key-pair service user
-- (no password or MFA prompts) and the analyst roles used to test row-level security.
-- NOT EXECUTED in this repo's CI: written for a Snowflake 30-day trial account. Run once as ACCOUNTADMIN.
create warehouse if not exists FUNDLAKE_WH warehouse_size = 'XSMALL' auto_suspend = 60 auto_resume = true initially_suspended = true;
create database if not exists FUNDLAKE;
create role if not exists FUNDLAKE_ROLE;
grant usage, operate on warehouse FUNDLAKE_WH to role FUNDLAKE_ROLE;
grant all on database FUNDLAKE to role FUNDLAKE_ROLE;
create schema if not exists FUNDLAKE.RAW;
create schema if not exists FUNDLAKE.BRONZE;
create schema if not exists FUNDLAKE.SILVER;
create schema if not exists FUNDLAKE.GOLD;
create schema if not exists FUNDLAKE.SECURITY;
grant all on all schemas in database FUNDLAKE to role FUNDLAKE_ROLE;
-- then: grant role FUNDLAKE_ROLE to user <your user>;
