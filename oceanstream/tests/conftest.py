import sys
from pathlib import Path

# After flattening, ensure the package root is importable (module directories live directly under project root)
_pkg_root = Path(__file__).resolve().parents[1]
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))
