import os

import pytest

from lull.ledger import Ledger
from lull.tf import LIGHTNING, TokenFactory

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("LULL_LIVE") != "1", reason="set LULL_LIVE=1 to run"),
]


def test_lightning_smoke(tmp_path):
    ledger = Ledger(tmp_path / "live.db")
    client = TokenFactory.from_settings(ledger)
    out = client.text(
        LIGHTNING,
        [{"role": "user", "content": "Reply with the single word: ok"}],
        run_id="live", stage="smoke", role="smoke", max_tokens=50, estimate=0.001,
    )  # fmt: skip
    assert out.strip()
    recs = ledger.calls("live")
    assert recs and recs[0].cost_usd > 0
