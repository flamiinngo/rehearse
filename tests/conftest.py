import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_package():
    # The repository root is the plugin package; import it as ``rehearse`` whatever the clone is called.
    spec = importlib.util.spec_from_file_location("rehearse", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["rehearse"] = module
    spec.loader.exec_module(module)


if "rehearse" not in sys.modules:
    _load_package()
