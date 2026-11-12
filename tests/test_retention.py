"""Politique de rétention : simulation, archivage froid, suppression du brut à 90 jours."""

import tarfile
from datetime import date

from gazette.storage import retention

TODAY = date(2026, 12, 31)


def _make_day(base, day: str) -> None:
    (base / day / "pqn").mkdir(parents=True)
    (base / day / "pqn" / "s.json").write_text("{}", encoding="utf-8")


def test_plan_touches_nothing_and_apply_archives(settings):
    _make_day(settings.raw_dir, "2026-12-30")  # récent : reste chaud
    _make_day(settings.raw_dir, "2026-11-01")  # > 30 j : archivé
    old_archive = settings.archive_dir / "raw" / "2026-09-01.tar.gz"  # > 90 j : supprimé
    old_archive.parent.mkdir(parents=True)
    old_archive.write_bytes(b"")

    actions = retention.plan(settings, TODAY)
    kinds = {(a.kind, a.target.replace("\\", "/").split("/")[-1]) for a in actions}
    assert ("archive", "2026-11-01") in kinds
    assert ("delete", "2026-09-01.tar.gz") in kinds
    assert not any(t == "2026-12-30" for _, t in kinds)
    assert (settings.raw_dir / "2026-11-01").exists()  # simulation : rien n'a bougé

    retention.apply(settings, TODAY, use_db=False)
    assert not (settings.raw_dir / "2026-11-01").exists()
    assert (settings.raw_dir / "2026-12-30").exists()
    archive = settings.archive_dir / "raw" / "2026-11-01.tar.gz"
    with tarfile.open(archive) as tar:
        assert "2026-11-01/pqn/s.json" in tar.getnames()
    assert not old_archive.exists()
