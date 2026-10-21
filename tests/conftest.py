import pathlib
import sys

# posting.py lives in syncer/ and deliberately imports nothing from its siblings,
# so it can be imported directly without dragging in the module named `secrets`
# that shadows the stdlib one (EDGE_CASES.md X1).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "syncer"))
