"""Find snapshot images written by an external detector for a recording."""

from __future__ import annotations

import datetime as dt
import functools
import logging
import os
import re
import string
from collections.abc import Iterable
from pathlib import Path

from homeassistant.util import slugify

_LOGGER = logging.getLogger(__name__)

# Placeholders allowed in the snapshot directory template
DIRECTORY_FIELDS = ("camera", "camera_slug", "channel", "date")

# strftime-like directives allowed in the snapshot file name pattern
_DIRECTIVES = {
    "Y": r"\d{4}",
    "y": r"\d{2}",
    "m": r"\d{2}",
    "d": r"\d{2}",
    "H": r"\d{2}",
    "M": r"\d{2}",
    "S": r"\d{2}",
    "f": r"\d{1,6}",
    "s": r"\d+(?:\.\d+)?",
}


@functools.lru_cache(maxsize=8)
def compile_name_pattern(pattern: str) -> re.Pattern[str]:
    """Convert a file name pattern into a regular expression.

    The pattern is matched against the whole file name. It supports the
    directives %Y %y %m %d %H %M %S %f (local time), %s (Unix timestamp),
    %% (literal percent sign) and the wildcards * and ?.

    Raises ValueError if the pattern is invalid or has no complete timestamp.
    """
    parts: list[str] = []
    used: set[str] = set()
    chars = iter(pattern)
    for char in chars:
        if char == "%":
            directive = next(chars, "")
            if directive == "%":
                parts.append("%")
            elif directive in _DIRECTIVES:
                if directive in used:
                    raise ValueError(f"Directive %{directive} is used twice")
                used.add(directive)
                parts.append(f"(?P<{directive}>{_DIRECTIVES[directive]})")
            else:
                raise ValueError(f"Unsupported directive %{directive}")
        elif char == "*":
            parts.append(".*")
        elif char == "?":
            parts.append(".")
        else:
            parts.append(re.escape(char))

    has_date_time = used & {"Y", "y"} and {"m", "d", "H", "M"} <= used
    if "s" not in used and not has_date_time:
        raise ValueError(
            "Pattern must contain %s or a date and time (%Y or %y, %m, %d, %H, %M)"
        )
    return re.compile("".join(parts))


def parse_timestamp(match: re.Match[str], tz: dt.tzinfo) -> dt.datetime:
    """Return the timezone-aware timestamp of a matched file name.

    Local date and time directives are interpreted in the given time zone.
    Raises ValueError if the values do not form a valid date.
    """
    values = match.groupdict()
    if values.get("s"):
        return dt.datetime.fromtimestamp(float(values["s"]), dt.UTC)

    year = int(values["Y"]) if values.get("Y") else 2000 + int(values["y"])
    microsecond = int(values["f"].ljust(6, "0")) if values.get("f") else 0
    return dt.datetime(
        year,
        int(values["m"]),
        int(values["d"]),
        int(values["H"]),
        int(values["M"]),
        int(values.get("S") or 0),
        microsecond,
        tzinfo=tz,
    )


def validate_directory_template(template: str) -> None:
    """Raise ValueError if the directory template cannot be used."""
    for _, field, _, _ in string.Formatter().parse(template):
        if field is not None and field not in DIRECTORY_FIELDS:
            raise ValueError(f"Unknown placeholder {{{field}}}")
    sample = render_directory(template, "Camera", 0, dt.date(2000, 1, 1))
    if not sample.is_absolute():
        raise ValueError("Directory must be an absolute path")


def render_directory(template: str, camera: str, channel: int, date: dt.date) -> Path:
    """Fill in the placeholders of the directory template."""
    return Path(
        template.format(
            camera=camera,
            camera_slug=slugify(camera),
            channel=channel,
            date=date,
        )
    )


def recording_window(
    start: dt.datetime, end: dt.datetime, tolerance: dt.timedelta, tz: dt.tzinfo
) -> tuple[dt.datetime, dt.datetime]:
    """Return the time window in which a snapshot belongs to a recording.

    Recording times without time zone are assumed to be in the given zone.
    """
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    if end.tzinfo is None:
        end = end.replace(tzinfo=tz)
    return start - tolerance, end + tolerance


def snapshot_directories(
    template: str,
    camera: str,
    channel: int,
    start: dt.datetime,
    end: dt.datetime,
    tz: dt.tzinfo,
) -> list[Path]:
    """Return the directories to search for a time window.

    With a {date} placeholder every local day touched by the window is
    searched, so recordings spanning midnight are handled.
    """
    first = start.astimezone(tz).date()
    last = end.astimezone(tz).date()
    directories: list[Path] = []
    day = first
    while day <= last:
        directory = render_directory(template, camera, channel, day)
        if directory not in directories:
            directories.append(directory)
        day += dt.timedelta(days=1)
    return directories


def find_snapshot(
    directories: Iterable[Path],
    pattern: re.Pattern[str],
    tz: dt.tzinfo,
    start: dt.datetime,
    end: dt.datetime,
) -> Path | None:
    """Return the earliest snapshot taken within [start, end], if any.

    Snapshots with the same timestamp are ordered by file name.
    This function does blocking I/O.
    """
    best: tuple[dt.datetime, str, Path] | None = None
    for directory in directories:
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    match = pattern.fullmatch(entry.name)
                    if match is None or not entry.is_file():
                        continue
                    try:
                        timestamp = parse_timestamp(match, tz)
                    except (ValueError, OverflowError):
                        continue
                    if start <= timestamp <= end and (
                        best is None or (timestamp, entry.name) < best[:2]
                    ):
                        best = (timestamp, entry.name, Path(entry.path))
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as err:
            _LOGGER.warning("Cannot read snapshot directory %s: %s", directory, err)
    return best[2] if best else None
