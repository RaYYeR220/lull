"""A live run id is the read capability for a visitor's upload (a finished run replays without a
token), so its random part must not be guessable: at least 96 bits. Ids of the old format
(`live-YYYYmmdd-HHMMSS-<6 hex>`) stay readable; the path guard still refuses traversal."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from lull.api import store as store_mod
from lull.api.live import new_run_id
from lull.api.store import RUN_ID_RE

NEW_FORMAT = re.compile(r"live-(\d{8})-([0-9a-f]+)")
OLD_ID = "live-20261027-235859-d4ec8c"
NEW_ID = "live-20261028-" + "0123456789abcdef01234567"


def test_new_run_ids_carry_at_least_96_random_bits():
    before = datetime.now(UTC) - timedelta(seconds=1)
    ids = [new_run_id() for _ in range(500)]
    after = datetime.now(UTC) + timedelta(seconds=1)
    assert len(set(ids)) == len(ids)
    days = {f"{before:%Y%m%d}", f"{after:%Y%m%d}"}
    for rid in ids:
        m = NEW_FORMAT.fullmatch(rid)
        assert m is not None, rid
        assert m.group(1) in days
        assert len(m.group(2)) * 4 >= 96  # hex: 4 bits a character
        assert RUN_ID_RE.fullmatch(rid)
    # no character position is fixed across ids: the suffix is random, not a clock
    suffixes = [NEW_FORMAT.fullmatch(r).group(2) for r in ids]
    for i in range(len(suffixes[0])):
        assert len({s[i] for s in suffixes}) > 1, i


@pytest.mark.parametrize("rid", [OLD_ID, NEW_ID])
def test_old_and_new_ids_are_both_served(client, data, make_run, settings, rid):
    make_run(data / "runs", rid)
    assert RUN_ID_RE.fullmatch(rid)
    assert store_mod.RunStore(settings).find(rid) == ("live", data / "runs" / rid)
    assert client.get(f"/v1/runs/{rid}").status_code == 200
    assert client.get(f"/v1/runs/{rid}/files/report.html").status_code == 200


@pytest.mark.parametrize(
    "rid",
    [
        "..",
        ".",
        f"../{NEW_ID}",
        f"{NEW_ID}/..",
        f"{NEW_ID}\n",
        "live-20261028-" + "a" * 100,  # longer than any id
        f"{NEW_ID}:$DATA",
        f"{NEW_ID}\\..",
    ],
)
def test_the_path_guard_still_refuses_odd_ids(data, make_run, settings, rid):
    make_run(data / "runs", NEW_ID)
    assert store_mod.RunStore(settings).find(rid) is None


@pytest.mark.parametrize("path", ["%2e%2e", f"%2e%2e/{NEW_ID}", f"{NEW_ID}%2f..", f"{NEW_ID}%00"])
def test_encoded_traversal_is_still_a_404(client, data, make_run, path):
    make_run(data / "runs", NEW_ID)
    assert client.get(f"/v1/runs/{path}").status_code == 404
    assert client.get(f"/v1/runs/{path}/files/report.html").status_code == 404
