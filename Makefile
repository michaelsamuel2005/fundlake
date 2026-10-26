.PHONY: install data pipeline test notes all snowflake-check spark-check
install:
	pip install -e ".[agent,snowflake,dev]"
data:
	PYTHONPATH=src python -c "from fundlake.synth import generate; print(generate('data/raw')['counts'])"
pipeline:
	PYTHONPATH=src python -m fundlake.pipeline
test:
	python -m pytest -q
all:
	python scripts/run_all.py && python scripts/build_notes.py
snowflake-check:       # renders the Snowflake SQL offline and parses it; does NOT connect
	python -m pytest -q tests/test_snowflake_dialect.py
spark-check:           # needs PySpark and Java
	python -m pytest -q tests/test_spark_parity.py
