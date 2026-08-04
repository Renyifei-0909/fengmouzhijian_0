from __future__ import annotations

import pytest

from scripts.postgres_acceptance import DATABASE_URL_ENV, AcceptanceRefusal
from scripts.postgres_contention_observe import main, observe_contention


def test_contention_main_exits_2_without_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)
    assert main([]) == 2
    err = capsys.readouterr().err
    assert "refused" in err
    assert DATABASE_URL_ENV in err


def test_contention_rejects_non_acceptance_shape() -> None:
    with pytest.raises(AcceptanceRefusal):
        observe_contention(jobs=1, workers=1, waves=1)
    with pytest.raises(AcceptanceRefusal):
        observe_contention(jobs=8, workers=4, waves=0)
    with pytest.raises(AcceptanceRefusal):
        observe_contention(jobs=8, workers=4, waves=17)
