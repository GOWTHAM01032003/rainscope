"""Offline coordinate-to-IANA time zone lookup.

TimezoneFinder's bundled boundary data is derived from timezone-boundary-builder.
Coordinates are processed locally and are never sent to a location service.
"""

from __future__ import annotations

from functools import lru_cache
from threading import Lock


class TimezoneLookupUnavailable(RuntimeError):
    pass


_finder = None
_finder_lock = Lock()


@lru_cache(maxsize=1024)
def timezones_for_coordinates(latitude: float, longitude: float) -> tuple[str, ...]:
    global _finder
    if _finder is None:
        with _finder_lock:
            if _finder is None:
                try:
                    from timezonefinder import TimezoneFinder
                    _finder = TimezoneFinder(in_memory=True)
                except Exception as exc:
                    raise TimezoneLookupUnavailable(
                        "Install project requirements and verify the bundled boundary data to enable offline location time zones."
                    ) from exc
    # timezones_at tests candidate polygon membership. The first item is the
    # library's documented default (smallest-area zone for overlaps).
    try:
        with _finder_lock:
            return tuple(_finder.timezones_at(lat=latitude, lng=longitude))
    except Exception as exc:
        raise TimezoneLookupUnavailable("The offline time-zone boundary lookup failed.") from exc
