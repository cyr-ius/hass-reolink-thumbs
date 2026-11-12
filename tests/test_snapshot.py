"""Tests for matching external snapshots to recordings."""

import datetime as dt
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from custom_components.reolink_thumbs.snapshot import (
    compile_name_pattern,
    find_snapshot,
    parse_timestamp,
    recording_window,
    snapshot_directories,
    validate_directory_template,
)

BERLIN = ZoneInfo("Europe/Berlin")


def _touch(directory: Path, *names: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(b"")


@pytest.mark.parametrize(
    ("pattern", "name", "expected"),
    [
        (
            "Snapshot_%Y%m%d_%H%M%S*",
            "Snapshot_20261108_123456_person.jpg",
            dt.datetime(2026, 11, 8, 12, 34, 56, tzinfo=BERLIN),
        ),
        (
            "%y-%m-%d %H.%M.%S.%f.jpg",
            "26-11-08 12.34.56.5.jpg",
            dt.datetime(2026, 11, 8, 12, 34, 56, 500000, tzinfo=BERLIN),
        ),
        (
            "cam?_%Y%m%d%H%M.jpg",
            "cam1_202611081234.jpg",
            dt.datetime(2026, 11, 8, 12, 34, tzinfo=BERLIN),
        ),
        (
            "*-%s-*.jpg",
            "front-1762601696.475377-abc.jpg",
            dt.datetime(2025, 11, 8, 11, 34, 56, 475377, tzinfo=dt.UTC),
        ),
        (
            "100%%_%Y%m%d_%H%M%S.png",
            "100%_20261108_123456.png",
            dt.datetime(2026, 11, 8, 12, 34, 56, tzinfo=BERLIN),
        ),
    ],
)
def test_pattern_parses_timestamp(
    pattern: str, name: str, expected: dt.datetime
) -> None:
    """Test supported directives, wildcards and escapes."""
    match = compile_name_pattern(pattern).fullmatch(name)
    assert match is not None
    assert parse_timestamp(match, BERLIN) == expected


def test_pattern_matches_whole_name() -> None:
    """Test that literal parts and suffixes must match."""
    pattern = compile_name_pattern("Snapshot_%Y%m%d_%H%M%S_person*")
    assert pattern.fullmatch("Snapshot_20261108_123456_person_sonja.jpg")
    assert not pattern.fullmatch("Snapshot_20261108_123456_cat.jpg")
    assert not pattern.fullmatch("old_Snapshot_20261108_123456_person.jpg")


@pytest.mark.parametrize(
    "pattern",
    ["Snapshot_%Y%m%d.jpg", "%Y%m%d_%H%M%S_%Y", "%Y%m%d_%H%M%Q", "%Y%m%d_%H%M%"],
)
def test_invalid_pattern(pattern: str) -> None:
    """Test that incomplete or unknown directives are rejected."""
    with pytest.raises(ValueError):
        compile_name_pattern(pattern)


def test_invalid_date_raises() -> None:
    """Test that impossible dates raise ValueError."""
    match = compile_name_pattern("%Y%m%d_%H%M%S").fullmatch("20261332_250000")
    assert match is not None
    with pytest.raises(ValueError):
        parse_timestamp(match, BERLIN)


@pytest.mark.parametrize(
    "template",
    ["relative/{camera}", "/media/{unknown}", "/media/{0}", "/media/{camera"],
)
def test_invalid_directory_template(template: str) -> None:
    """Test that relative paths and unknown placeholders are rejected."""
    with pytest.raises(ValueError):
        validate_directory_template(template)


def test_directories_span_midnight() -> None:
    """Test placeholders and that every local day of the window is searched."""
    start = dt.datetime(2026, 11, 7, 23, 59, 50, tzinfo=BERLIN)
    end = dt.datetime(2026, 11, 8, 0, 0, 20, tzinfo=BERLIN)
    assert snapshot_directories(
        "/media/{camera_slug}/{channel:02d}/{date:%Y/%m/%d}",
        "Kamera Süd",
        1,
        start,
        end,
        BERLIN,
    ) == [
        Path("/media/kamera_sud/01/2026/11/07"),
        Path("/media/kamera_sud/01/2026/11/08"),
    ]
    assert snapshot_directories("/media/{camera}", "Garage", 0, start, end, BERLIN) == [
        Path("/media/Garage")
    ]


def test_recording_window_naive_and_aware() -> None:
    """Test the tolerance and that naive recording times use the given zone."""
    tolerance = dt.timedelta(seconds=5)
    start, end = recording_window(
        dt.datetime(2026, 11, 8, 12, 0, 0),
        dt.datetime(2026, 11, 8, 12, 1, 0, tzinfo=dt.UTC),
        tolerance,
        BERLIN,
    )
    assert start == dt.datetime(2026, 11, 8, 11, 59, 55, tzinfo=BERLIN)
    assert end == dt.datetime(2026, 11, 8, 12, 1, 5, tzinfo=dt.UTC)
    assert recording_window(
        dt.datetime(2026, 11, 8, 12, 0, 0),
        dt.datetime(2026, 11, 8, 12, 1, 0),
        dt.timedelta(0),
        BERLIN,
    ) == (
        dt.datetime(2026, 11, 8, 12, 0, 0, tzinfo=BERLIN),
        dt.datetime(2026, 11, 8, 12, 1, 0, tzinfo=BERLIN),
    )


def test_find_earliest_snapshot_in_window(tmp_path: Path) -> None:
    """Test the window boundaries and that the earliest snapshot wins."""
    day = tmp_path / "2026-11-08"
    _touch(
        day,
        "Snapshot_20261108_115959_person.jpg",
        "Snapshot_20261108_120010_person_b.jpg",
        "Snapshot_20261108_120010_person_a.jpg",
        "Snapshot_20261108_120000_cat.jpg",
        "Snapshot_20261108_120100_person.jpg",
        "Snapshot_20261108_120101_person.jpg",
        "Snapshot_20261399_120000_person.jpg",
    )
    (day / "Snapshot_20261108_120005_person_dir").mkdir()
    pattern = compile_name_pattern("Snapshot_%Y%m%d_%H%M%S_person*")
    start = dt.datetime(2026, 11, 8, 12, 0, 0, tzinfo=BERLIN)
    end = dt.datetime(2026, 11, 8, 12, 1, 0, tzinfo=BERLIN)

    found = find_snapshot([tmp_path / "missing", day], pattern, BERLIN, start, end)
    assert found == day / "Snapshot_20261108_120010_person_a.jpg"

    assert find_snapshot([day], pattern, BERLIN, end, end) == (
        day / "Snapshot_20261108_120100_person.jpg"
    )
    later = end + dt.timedelta(minutes=5)
    assert find_snapshot([day], pattern, BERLIN, later, later) is None


def test_find_snapshot_across_dst_change(tmp_path: Path) -> None:
    """Test local file names around the end of daylight saving time."""
    _touch(tmp_path, "Snapshot_20261025_023000.jpg")
    pattern = compile_name_pattern("Snapshot_%Y%m%d_%H%M%S*")
    # 02:30 local exists twice; file names are read as the first occurrence (CEST)
    cest_start = dt.datetime(2026, 10, 25, 0, 29, 0, tzinfo=dt.UTC)
    cest_end = dt.datetime(2026, 10, 25, 0, 31, 0, tzinfo=dt.UTC)
    assert find_snapshot([tmp_path], pattern, BERLIN, cest_start, cest_end)
    cet_start = cest_start + dt.timedelta(hours=1)
    cet_end = cest_end + dt.timedelta(hours=1)
    assert find_snapshot([tmp_path], pattern, BERLIN, cet_start, cet_end) is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any directory")
def test_unreadable_directory_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that an unreadable directory is skipped with a warning."""
    locked = tmp_path / "locked"
    _touch(locked, "Snapshot_20261108_120000.jpg")
    locked.chmod(0)
    try:
        pattern = compile_name_pattern("Snapshot_%Y%m%d_%H%M%S*")
        start = dt.datetime(2026, 11, 8, 12, 0, tzinfo=BERLIN)
        assert find_snapshot([locked], pattern, BERLIN, start, start) is None
    finally:
        locked.chmod(0o755)
    assert "Cannot read snapshot directory" in caplog.text
