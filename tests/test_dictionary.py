from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_every_silver_and_gold_column_is_defined(con):
    cols = yaml.safe_load((ROOT / "docs/columns.yaml").read_text())
    have = {r[0] for r in con.execute("select distinct column_name from information_schema.columns where table_schema in ('silver','gold')").fetchall()}
    assert not (have - set(cols)), f"undefined columns: {sorted(have - set(cols))}"
    assert all(len(str(v)) > 8 for v in cols.values())


def test_dictionary_file_is_current(built):
    import importlib.util
    spec = importlib.util.spec_from_file_location("gd", ROOT / "scripts/gen_dictionary.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    fresh = m.build(built["db"])
    assert "MISSING" not in fresh
    assert (ROOT / "docs/DATA_DICTIONARY.md").read_text() == fresh, "run: python scripts/gen_dictionary.py"
