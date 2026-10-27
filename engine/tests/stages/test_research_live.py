import os

import pytest

from lull.ledger import Ledger
from lull.tavily import Tavily

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("LULL_LIVE") != "1", reason="set LULL_LIVE=1 to run"),
]


def test_tavily_search_live(tmp_path):
    ledger = Ledger(tmp_path / "live.db")
    tv = Tavily.from_settings(ledger)
    assert tv is not None, "TAVILY_API_KEY is not set"
    try:
        results = tv.search("Elephants Dream Blender open movie", run_id="live")
    finally:
        tv.close()
    assert len(results) >= 1 and results[0]["url"].startswith("http")
