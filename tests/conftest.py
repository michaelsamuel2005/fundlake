import json
import shutil
import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fundlake import pipeline as P  # noqa: E402

RAW = ROOT / "data" / "raw"


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    """Run the whole pipeline once on the committed synthetic data."""
    d = tmp_path_factory.mktemp("build")
    cfg = P.Config(RAW, d / "fundlake.duckdb", work_dir=d / "work", now_utc="2026-10-26T00:00:00+00:00")
    info = P.run_pipeline(cfg)
    return dict(cfg=cfg, info=info, db=cfg.db_path)


@pytest.fixture(scope="session")
def con(built):
    c = duckdb.connect(str(built["db"]), read_only=True)
    yield c
    c.close()


@pytest.fixture(scope="session")
def key():
    return json.loads((RAW / "answer_key.json").read_text())


@pytest.fixture()
def raw_copy(tmp_path):
    dst = tmp_path / "raw"
    shutil.copytree(RAW, dst)
    return dst
