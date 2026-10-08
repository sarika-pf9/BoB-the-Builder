import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Never let a developer's real bob.env or BOB_* env leak into tests.
for key in [k for k in os.environ if k.startswith("BOB_")]:
    del os.environ[key]
os.environ["BOB_CONFIG"] = str(ROOT / "tests" / "does-not-exist.env")
os.environ["BOB_QUAY_NAMESPACE"] = "testns"

sys.path.insert(0, str(ROOT / "builder"))
