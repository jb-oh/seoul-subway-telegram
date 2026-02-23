"""Client for the Seoul Open Data subway timetable API.

Provides scheduled timetable lookup for Seoul Metro lines (1-8호선).
Uses SearchInfoBySubwayNameService to resolve station names to FR_CODE,
then SearchSTNTimeTableByFRCodeService for timetable data.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp

logger = logging.getLogger(__name__)

TIMETABLE_API_BASE = "http://openAPI.seoul.go.kr:8088"
KST = ZoneInfo("Asia/Seoul")

# Lines with timetable data available in SearchSTNTimeTableByFRCodeService.
# 1, 2, 5호선 and Korail lines (수인분당선, 경의선 etc.) are NOT covered.
SUPPORTED_LINES = {"3호선", "4호선", "6호선", "7호선", "8호선"}

# Mapping from API LINE_NUM format (e.g. "02호선") to our internal names
_LINE_NUM_ALIASES: dict[str, str] = {}
for _n in range(1, 10):
    _LINE_NUM_ALIASES[f"0{_n}호선"] = f"{_n}호선"
    _LINE_NUM_ALIASES[f"{_n}호선"] = f"{_n}호선"


@dataclass
class TimetableEntry:
    """A single scheduled train departure."""

    train_no: str
    destination: str
    departure_time: str  # HH:MM:SS
    arrival_time: str  # HH:MM:SS
    is_express: bool

    @property
    def departure_display(self) -> str:
        """Format departure time as HH:MM."""
        parts = self.departure_time.split(":")
        if len(parts) >= 2:
            return f"{parts[0]}:{parts[1]}"
        return self.departure_time


# Cache: (station_name, line_name_or_empty) -> fr_code
_fr_code_cache: dict[tuple[str, str], str] = {}


async def _lookup_station_info(api_key: str, station_name: str) -> list[dict]:
    """Fetch station info entries by station name."""
    url = (
        f"{TIMETABLE_API_BASE}/{api_key}/json/"
        f"SearchInfoBySubwayNameService/1/20/{station_name}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    logger.error("Station info API returned status %d", resp.status)
                    return []
                data = await resp.json(content_type=None)
    except Exception:
        logger.exception("Failed to fetch station info for %s", station_name)
        return []

    svc = data.get("SearchInfoBySubwayNameService")
    if not svc:
        return []
    return svc.get("row", [])


def _normalize_api_line(line_num: str) -> str:
    """Normalize LINE_NUM from the API to our line name format."""
    return _LINE_NUM_ALIASES.get(line_num, line_num)


async def get_station_fr_code(
    api_key: str, station_name: str, line_name: str | None = None
) -> tuple[str, str] | None:
    """Look up FR_CODE for a station, optionally filtering by line.

    Returns:
        (fr_code, resolved_line_name) or None if not found.
    """
    cache_key = (station_name, line_name or "")
    if cache_key in _fr_code_cache:
        fr_code = _fr_code_cache[cache_key]
        return (fr_code, line_name or "")

    rows = await _lookup_station_info(api_key, station_name)
    if not rows:
        return None

    for row in rows:
        api_line = _normalize_api_line(row.get("LINE_NUM", ""))
        fr_code = row.get("FR_CODE", "")
        if not fr_code:
            continue
        if line_name is None or api_line == line_name:
            _fr_code_cache[cache_key] = fr_code
            return (fr_code, api_line)

    return None


def get_weekday_type() -> tuple[int, str]:
    """Return (api_code, label) for the current day type in KST.

    Codes: 1 = 평일, 2 = 토요일, 3 = 일요일/공휴일.
    """
    now = datetime.now(KST)
    if now.weekday() == 5:
        return 2, "토요일"
    elif now.weekday() == 6:
        return 3, "일요일/공휴일"
    return 1, "평일"


def direction_to_code(direction: str) -> int:
    """Convert direction string to API code (1=상행/내선, 2=하행/외선)."""
    if direction in ("상행", "내선"):
        return 1
    return 2


def direction_code_to_label(code: int, line_name: str) -> str:
    """Convert API direction code back to a Korean label."""
    if line_name == "2호선":
        return "내선" if code == 1 else "외선"
    return "상행" if code == 1 else "하행"


async def get_timetable(
    api_key: str,
    fr_code: str,
    weekday: int,
    direction_code: int,
) -> list[TimetableEntry]:
    """Fetch the full daily timetable for a station.

    Args:
        api_key: Seoul Open Data API key.
        fr_code: Station external code (FR_CODE).
        weekday: 1=weekday, 2=Saturday, 3=Sunday/holiday.
        direction_code: 1=상행/내선, 2=하행/외선.

    Returns:
        List of TimetableEntry sorted by departure time.
    """
    url = (
        f"{TIMETABLE_API_BASE}/{api_key}/json/"
        f"SearchSTNTimeTableByFRCodeService/1/500/"
        f"{fr_code}/{weekday}/{direction_code}"
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    logger.error("Timetable API returned status %d", resp.status)
                    return []
                data = await resp.json(content_type=None)
    except Exception:
        logger.exception("Failed to fetch timetable for FR_CODE %s", fr_code)
        return []

    svc = data.get("SearchSTNTimeTableByFRCodeService")
    if not svc:
        return []

    raw_list = svc.get("row", [])
    results = []
    for item in raw_list:
        entry = TimetableEntry(
            train_no=item.get("TRAIN_NO", ""),
            destination=item.get("SUBWAYENAME", ""),
            departure_time=item.get("LEFTTIME", ""),
            arrival_time=item.get("ARRIVETIME", ""),
            is_express=item.get("EXPRESS_YN", "G") not in ("G", ""),
        )
        results.append(entry)

    results.sort(key=lambda e: e.departure_time)
    return results


def get_upcoming(
    timetable: list[TimetableEntry], count: int = 5
) -> list[TimetableEntry]:
    """Return the next *count* trains departing from now (KST)."""
    now = datetime.now(KST).strftime("%H:%M:%S")
    upcoming = [e for e in timetable if e.departure_time >= now]
    return upcoming[:count]


def get_first_last(
    timetable: list[TimetableEntry],
) -> tuple[TimetableEntry | None, TimetableEntry | None]:
    """Return (first_train, last_train) from the timetable."""
    if not timetable:
        return None, None
    return timetable[0], timetable[-1]
