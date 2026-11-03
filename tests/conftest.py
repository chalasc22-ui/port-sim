import copy
import json
from pathlib import Path

import pytest

from terminal_layout import LayoutError, load_layout
from terminal_layout.loader import layout_from_dict

ROOT = Path(__file__).resolve().parent.parent
DEMO_PATH = ROOT / "layouts" / "demo-12x8.json"
DAMES_PATH = ROOT / "layouts" / "dames-point.json"


@pytest.fixture(scope="session")
def demo_path() -> Path:
    return DEMO_PATH


@pytest.fixture(scope="session")
def demo():
    return load_layout(DEMO_PATH)


@pytest.fixture(scope="session")
def dames_path() -> Path:
    return DAMES_PATH


@pytest.fixture(scope="session")
def dames():
    return load_layout(DAMES_PATH)


@pytest.fixture
def data():
    """A fresh, editable copy of the authored demo layout."""
    return json.loads(DEMO_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def broken():
    """Returns the error lines a mutated copy of the demo produces ([] if it loads)."""

    def run(mutate):
        edited = copy.deepcopy(json.loads(DEMO_PATH.read_text(encoding="utf-8")))
        mutate(edited)
        try:
            layout_from_dict(edited)
        except LayoutError as exc:
            return list(exc.errors)
        return []

    return run
