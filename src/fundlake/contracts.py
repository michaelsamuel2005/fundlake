"""Data contracts: structural checks on landed files before anything is built. Semantic faults (breaks, stale data) are the
job of dbt and the controls; contracts catch schema drift, nulls, type and enum violations and invalid identifiers."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import yaml

from .synth import cusip_valid, isin_valid, sedol_valid

CONTRACT_DIR = Path(__file__).resolve().parents[2] / "contracts"
CHECKS = {"isin": isin_valid, "sedol": sedol_valid, "cusip": cusip_valid}


def load_contract(source: str, contract_dir: Path = CONTRACT_DIR) -> dict:
    return yaml.safe_load((contract_dir / f"{source}.yaml").read_text())


def contract_hash(contract_dir: Path = CONTRACT_DIR) -> str:
    h = hashlib.sha256()
    for p in sorted(contract_dir.glob("*.yaml")):
        h.update(p.read_bytes())
    return h.hexdigest()


def _bad_type(s: pd.Series, typ: str) -> pd.Series:
    s = s.dropna()
    if typ == "decimal":
        return s[pd.to_numeric(s, errors="coerce").isna()]
    if typ == "integer":
        return s[pd.to_numeric(s, errors="coerce").isna() | (pd.to_numeric(s, errors="coerce") % 1 != 0)]
    if typ in ("date", "timestamp"):
        return s[pd.to_datetime(s, errors="coerce").isna()]
    return s.iloc[0:0]


def validate_file(path: Path, contract: dict) -> list[dict]:
    """Return a list of violations (empty = contract met)."""
    v: list[dict] = []
    df = pd.read_csv(path, dtype=str, keep_default_na=True)
    fields = contract["fields"]
    missing = [c for c in fields if c not in df.columns]
    extra = [c for c in df.columns if c not in fields]
    for c in missing:
        v.append(dict(rule="missing_column", field=c, detail=f"{c} absent from {path.name}"))
    for c in extra:
        v.append(dict(rule="unexpected_column", field=c, detail=f"{c} not in contract (schema drift)"))
    mn = contract.get("volume", {}).get("min_rows")
    if mn is not None and len(df) < mn:
        v.append(dict(rule="min_rows", field=None, detail=f"{len(df)} rows < {mn}"))
    for name, spec in fields.items():
        if name not in df.columns:
            continue
        s = df[name]
        n_null = int(s.isna().sum())
        if not spec.get("nullable", True) and n_null:
            v.append(dict(rule="not_null", field=name, detail=f"{n_null} nulls"))
        nn = s.dropna()
        bad = _bad_type(s, spec["type"])
        if len(bad):
            v.append(dict(rule="type", field=name, detail=f"{len(bad)} values not {spec['type']}, e.g. {bad.iloc[0]!r}"))
        if "enum" in spec:
            bad = nn[~nn.isin([str(x) for x in spec["enum"]])]
            if len(bad):
                v.append(dict(rule="enum", field=name, detail=f"{len(bad)} values outside enum, e.g. {bad.iloc[0]!r}"))
        if "pattern" in spec:
            bad = nn[~nn.str.match(spec["pattern"])]
            if len(bad):
                v.append(dict(rule="pattern", field=name, detail=f"{len(bad)} values fail {spec['pattern']}, e.g. {bad.iloc[0]!r}"))
        if "check" in spec:
            fn = CHECKS[spec["check"]]
            bad = nn[~nn.map(fn)]
            if len(bad):
                v.append(dict(rule=f"check_{spec['check']}", field=name, detail=f"{len(bad)} invalid check digits, e.g. {bad.iloc[0]!r}"))
        if "min" in spec and spec["type"] in ("decimal", "integer"):
            num = pd.to_numeric(nn, errors="coerce").dropna()
            bad = num[num < spec["min"]]
            if len(bad):
                v.append(dict(rule="min", field=name, detail=f"{len(bad)} values < {spec['min']}"))
    return v


def validate_all(raw_dir: Path, contract_dir: Path = CONTRACT_DIR) -> dict[str, list[dict]]:
    out = {}
    for p in sorted(contract_dir.glob("*.yaml")):
        if p.stem in ("gate", "access"):
            continue
        c = yaml.safe_load(p.read_text())
        f = Path(raw_dir) / f"{c['source']}.csv"
        out[c["source"]] = validate_file(f, c) if f.exists() else [dict(rule="missing_file", field=None, detail=f"{f.name} not delivered")]
    return out
