"""TEST FIXTURE ONLY: fake measured values, not actual XMOS resource measurements."""
import json
from pathlib import Path
Path("benchmark-verification.json").write_text(json.dumps({
    "passed": True, "metrics": {"threads_per_tile": {"0": 2}, "libraries": ["lib_xcore"]}
}))
