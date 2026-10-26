"""Run the evaluation with a REAL LLM. Needs an API key (ANTHROPIC_API_KEY or OPENAI_API_KEY) and the model name, e.g.
    python scripts/run_agent_live.py anthropic:<model-name>
This script has not been run by the author of this repo (no API key was available in the build sandbox); the CI evaluation uses the
scripted model. Results are written to outputs/agent_eval_live.json so they can be compared with outputs/agent_eval.json."""
import json
import sys
from pathlib import Path

from fundlake.agent.eval import evaluate

if __name__ == "__main__":
    model = sys.argv[1]
    root = Path(__file__).resolve().parents[1]
    r = evaluate(root / "data/fundlake.duckdb", root / "data/raw", root / "data/raw/answer_key.json", model, label=model)
    (root / "outputs").mkdir(exist_ok=True)
    (root / "outputs/agent_eval_live.json").write_text(json.dumps(r, indent=1))
    print({k: v for k, v in r.items() if k != "rows"})
