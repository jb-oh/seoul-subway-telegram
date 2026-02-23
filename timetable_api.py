"""Client for the Seoul Open Data subway timetable API.

Provides scheduled timetable lookup for Seoul Metro lines (3·4·6·7·8·9호선)
via SearchSTNTimeTableByFRCodeService, and metropolitan rail lines
(수인분당선, 경의중앙선, 공항철도, etc.) via the KRIC API.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp

logger = logging.getLogger(__name__)

TIMETABLE_API_BASE = "http://openAPI.seoul.go.kr:8088"
KRIC_API_BASE = "https://openapi.kric.go.kr/openapi/trainUseInfo/subwayTimetable"
KST = ZoneInfo("Asia/Seoul")

# Lines with timetable data in SearchSTNTimeTableByFRCodeService.
# 1, 2, 5호선 return empty results from this service.
SUPPORTED_LINES = {"3호선", "4호선", "6호선", "7호선", "8호선", "9호선"}

# Metropolitan/Korail lines served via KRIC API.
KRIC_LINES = {
    "수인분당선", "경의중앙선", "경춘선", "서해선", "경강선",
    "공항철도", "신분당선", "우이신설선", "신림선",
}

ALL_SUPPORTED_LINES = SUPPORTED_LINES | KRIC_LINES

# KRIC day code conversion: Seoul API (1=평일, 2=토, 3=일) → KRIC (8=평일, 7=토, 9=일)
_KRIC_DAY_CODE = {1: 8, 2: 7, 3: 9}

# KRIC (railOprIsttCd, lnCd) per line name.
# TODO: Verify exact codes after KRIC API key activation.
KRIC_LINE_CODES: dict[str, tuple[str, str]] = {
    "수인분당선": ("KR", "K2"),
    "경의중앙선": ("KR", "K4"),
    "경춘선":     ("KR", "K5"),
    "서해선":     ("KR", "K7"),
    "경강선":     ("KR", "K8"),
    "공항철도":   ("AREX", "A1"),
    "신분당선":   ("SBL", "D1"),
    "우이신설선": ("UI", "UI"),   # TBD
    "신림선":     ("SL", "SL"),   # TBD
}

# Static KRIC station code table: (station_name, line_name) → stinCd.
# Populate from KRIC station code Excel (data.kric.go.kr → 자료실)
# after obtaining KRIC_API_KEY.
_KRIC_STATION_CODES: dict[tuple[str, str], str] = {}

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


def get_station_kric_code(station_name: str, line_name: str) -> str | None:
    """Look up KRIC stinCd for a station on a KRIC-operated line.

    Returns stinCd or None if the station is not yet in the code table.
    Populate _KRIC_STATION_CODES from the KRIC Excel code list
    (data.kric.go.kr → 자료실 → 운영기관,노선,역 코드정보 리스트).
    """
    return _KRIC_STATION_CODES.get((station_name, line_name))


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


async def get_timetable_kric(
    kric_key: str,
    line_name: str,
    station_code: str,
    weekday: int,
    direction_code: int,
) -> list[TimetableEntry]:
    """Fetch timetable via KRIC API (metropolitan rail lines).

    Requires KRIC_API_KEY from openapi.kric.go.kr.
    Field names (trnNo, arvStinNm, dptTm, etc.) are provisional —
    confirm against live response after key activation.
    """
    if not kric_key:
        logger.warning("KRIC_API_KEY not configured; cannot fetch KRIC timetable")
        return []

    opr_cd, ln_cd = KRIC_LINE_CODES[line_name]
    kric_day = _KRIC_DAY_CODE[weekday]
    params = {
        "serviceKey": kric_key,
        "format": "json",
        "railOprIsttCd": opr_cd,
        "lnCd": ln_cd,
        "stinCd": station_code,
        "dayCd": str(kric_day),
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                KRIC_API_BASE,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    logger.error("KRIC API returned status %d", resp.status)
                    return []
                data = await resp.json(content_type=None)
    except Exception:
        logger.exception(
            "Failed to fetch KRIC timetable for %s %s", line_name, station_code
        )
        return []

    # TODO: Verify exact response structure after KRIC key activation.
    # Try common response shapes seen in KRIC/data.go.kr APIs.
    items = (
        data.get("body", {}).get("items", [])
        or data.get("response", {}).get("body", {}).get("items", [])
        or data.get("items", [])
        or []
    )
    if not items:
        logger.info(
            "No KRIC timetable data for %s %s (day=%s, dir=%s)",
            line_name, station_code, kric_day, direction_code,
        )
        return []

    if not isinstance(items, list):
        items = [items]

    results = []
    for item in items:
        # TODO: Confirm field names from live KRIC response.
        # Provisional mapping based on KRIC API documentation patterns.
        departure_time = item.get("dptTm", item.get("LEFTTIME", "00:00:00"))
        # KRIC times may be 6-digit (HHMMSS) — normalize to HH:MM:SS
        if len(departure_time) == 6 and ":" not in departure_time:
            departure_time = f"{departure_time[:2]}:{departure_time[2:4]}:{departure_time[4:]}"
        arrival_time = item.get("arvTm", item.get("ARRIVETIME", "00:00:00"))
        if len(arrival_time) == 6 and ":" not in arrival_time:
            arrival_time = f"{arrival_time[:2]}:{arrival_time[2:4]}:{arrival_time[4:]}"

        # Direction filter: KRIC may include both directions in one response.
        # TODO: Confirm field name after key activation (updnLine or similar).
        item_dir = item.get("updnLine", item.get("INOUT_TAG", ""))
        if item_dir and str(item_dir) != str(direction_code):
            continue

        entry = TimetableEntry(
            train_no=item.get("trnNo", item.get("TRAIN_NO", "")),
            destination=item.get("arvStinNm", item.get("SUBWAYENAME", "")),
            departure_time=departure_time,
            arrival_time=arrival_time,
            is_express=item.get("trnsRouteNm", "") in ("급행", "특급"),
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
