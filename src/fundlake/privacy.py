"""Row-level security and minimisation for the client view on DuckDB. Mirrors snowflake/rls.sql (which is not executed here)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

ACCESS = Path(__file__).resolve().parents[2] / "contracts" / "access.yaml"
PERSONAL_COLUMNS = {"client_name", "email", "date_of_birth", "dob", "postcode", "client_id"}


class AccessDenied(PermissionError):
    pass


def client_view(con, role: str, access_file: Path = ACCESS) -> pd.DataFrame:
    roles = yaml.safe_load(Path(access_file).read_text())["roles"]
    if role not in roles:
        raise AccessDenied(f"role {role!r} has no access to client_policy_view")
    df = con.execute("select * from gold.client_policy_view").fetchdf()
    assert not (PERSONAL_COLUMNS & set(df.columns)), "personal columns leaked into the minimised view"
    regions = roles[role]
    if "*" in regions:
        return df
    return df[df.region.isin(regions)].reset_index(drop=True)
