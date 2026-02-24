"""Client for the Seoul Open Data subway timetable API.

Provides scheduled timetable lookup for Seoul Metro lines (3·4·6·7·8·9호선)
via SearchSTNTimeTableByFRCodeService, and additional Seoul Metro lines
(1·2호선) via the KRIC openapi.kric.go.kr API (railOprIsttCd=S1).

KRIC API coverage confirmed:
  - Operator S1 (서울교통공사), lines 1 and 2 only.
  - Response: body is a list of items with fields trnNo, dptTm, arvTm,
    stinCd, lnCd, railOprIsttCd, dayCd, dayNm.
  - dptTm/arvTm are 6-digit HHMMSS strings (no colons).
  - No direction or destination fields — all trains returned regardless of
    direction; destination is not available.
  - Lines 5–8 and all Korail/AREX/SBL lines have no data in this API.
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

# Lines served via KRIC S1 API (서울교통공사).
# These return departure times only — no direction or destination fields.
KRIC_S1_LINES = {"1호선", "2호선"}

# Lines intended for future KRIC integration (currently no API data available).
# Korail-operated metro lines (수인분당선 etc.) are NOT in KRIC subwayTimetable.
KRIC_LINES = KRIC_S1_LINES | {
    "수인분당선", "경의중앙선", "경춘선", "서해선", "경강선",
    "공항철도", "신분당선", "우이신설선", "신림선",
}

ALL_SUPPORTED_LINES = SUPPORTED_LINES | KRIC_LINES

# KRIC day code conversion: Seoul API (1=평일, 2=토, 3=일) → KRIC (8=평일, 7=토, 9=일)
_KRIC_DAY_CODE = {1: 8, 2: 7, 3: 9}

# KRIC (railOprIsttCd, lnCd) per line name.
# Confirmed working: S1/1 (1호선), S1/2 (2호선).
# Korail lines (KR) have no data in KRIC subwayTimetable endpoint.
KRIC_LINE_CODES: dict[str, tuple[str, str]] = {
    "1호선":     ("S1", "1"),    # confirmed — Seoul core (서울역~청량리)
    "2호선":     ("S1", "2"),    # confirmed — full circular line
    "수인분당선": ("KR", "K2"),   # TODO: no KRIC data for KR lines yet
    "경의중앙선": ("KR", "K4"),
    "경춘선":     ("KR", "K5"),
    "서해선":     ("KR", "K7"),
    "경강선":     ("KR", "K8"),
    "공항철도":   ("AREX", "A1"),
    "신분당선":   ("SBL", "D1"),
    "우이신설선": ("UI", "UI"),
    "신림선":     ("SL", "SL"),
}

# Static KRIC station code table: (station_name, line_name) → stinCd.
#
# 1호선 codes (S1/1): Seoul Metro-operated segment only (서울역 ~ 청량리).
# Korail operates the extensions north of 도봉산 and south of 서울역;
# those stations have no data in KRIC subwayTimetable.
#
# 2호선 codes (S1/2): full circular line including branch lines.
# Outer ring: 201 (시청) → 242 (충정로) in 외선 direction.
# Branch — 성수지선: 244–247. Branch — 신도림지선: 248–250.
_KRIC_STATION_CODES: dict[tuple[str, str], str] = {
    # ── 1호선 (S1/1, stinCd 150–159) ──────────────────────────────────
    ("서울역",   "1호선"): "150",
    ("시청",     "1호선"): "151",
    ("종각",     "1호선"): "152",
    ("종로3가",  "1호선"): "153",
    ("종로5가",  "1호선"): "154",
    ("동대문",   "1호선"): "155",
    ("동묘앞",   "1호선"): "156",
    ("신설동",   "1호선"): "157",
    ("제기동",   "1호선"): "158",
    ("청량리",   "1호선"): "159",

    # ── 2호선 (S1/2, stinCd 201–250) ─────────────────────────────────
    # Outer ring (외선 direction, clockwise from 시청):
    ("시청",          "2호선"): "201",
    ("을지로입구",    "2호선"): "202",
    ("을지로3가",     "2호선"): "203",
    ("을지로4가",     "2호선"): "204",
    ("동대문역사문화공원", "2호선"): "205",
    ("신당",          "2호선"): "206",
    ("상왕십리",      "2호선"): "207",
    ("왕십리",        "2호선"): "208",
    ("한양대",        "2호선"): "209",
    ("뚝섬",          "2호선"): "210",
    ("성수",          "2호선"): "211",
    ("건대입구",      "2호선"): "212",
    ("구의",          "2호선"): "213",
    ("강변",          "2호선"): "214",
    ("잠실나루",      "2호선"): "215",
    ("잠실",          "2호선"): "216",
    ("잠실새내",      "2호선"): "217",
    ("종합운동장",    "2호선"): "218",
    ("삼성",          "2호선"): "219",
    ("선릉",          "2호선"): "220",
    ("역삼",          "2호선"): "221",
    ("강남",          "2호선"): "222",
    ("교대",          "2호선"): "223",
    ("방배",          "2호선"): "224",
    ("사당",          "2호선"): "225",
    ("낙성대",        "2호선"): "226",
    ("서울대입구",    "2호선"): "227",
    ("봉천",          "2호선"): "228",
    ("신림",          "2호선"): "229",
    ("신대방",        "2호선"): "230",
    ("구로디지털단지","2호선"): "231",
    ("대림",          "2호선"): "232",
    ("신도림",        "2호선"): "233",
    ("문래",          "2호선"): "234",
    ("영등포구청",    "2호선"): "235",
    ("당산",          "2호선"): "236",
    ("합정",          "2호선"): "237",
    ("홍대입구",      "2호선"): "238",
    ("신촌",          "2호선"): "239",
    ("이대",          "2호선"): "240",
    ("아현",          "2호선"): "241",
    ("충정로",        "2호선"): "242",
    # 성수지선 (from 성수):
    ("용답",          "2호선"): "244",
    ("신답",          "2호선"): "245",
    ("용두",          "2호선"): "246",
    ("용마산",        "2호선"): "247",
    # 신도림지선 (from 신도림):
    ("도림천",        "2호선"): "248",
    ("양천구청",      "2호선"): "249",
    ("신정네거리",    "2호선"): "250",
}

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
    """Fetch timetable via KRIC API.

    Confirmed working for lines in KRIC_S1_LINES (1호선, 2호선) via
    railOprIsttCd=S1.  Response structure (verified 2025):
      - body is a JSON array of objects (not a nested dict).
      - Fields: trnNo, dptTm, arvTm, stinCd, lnCd, railOprIsttCd, dayCd, dayNm.
      - dptTm / arvTm are 6-digit HHMMSS strings (no colons).
      - No direction or destination fields — all trains for the station/day
        are returned regardless of direction.

    For lines NOT in KRIC_S1_LINES (Korail lines, etc.) this returns []
    because _KRIC_STATION_CODES has no entries for them yet.
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

    result_code = data.get("header", {}).get("resultCode")
    if result_code != "00":
        logger.info(
            "KRIC API no data for %s %s (day=%s): resultCode=%s",
            line_name, station_code, kric_day, result_code,
        )
        return []

    # body is a JSON array directly (confirmed against live API).
    body = data.get("body", [])
    items: list[dict] = body if isinstance(body, list) else []
    if not items:
        return []

    def _norm_time(t: str | None) -> str:
        """Normalize 6-digit HHMMSS string (or None) → HH:MM:SS."""
        if not t:
            return ""
        if len(t) == 6 and ":" not in t:
            return f"{t[:2]}:{t[2:4]}:{t[4:]}"
        return t

    results = []
    for item in items:
        departure_time = _norm_time(item.get("dptTm"))
        if not departure_time:
            continue  # skip entries with no departure time
        arrival_time = _norm_time(item.get("arvTm"))

        entry = TimetableEntry(
            train_no=item.get("trnNo", ""),
            destination="",   # KRIC S1 response has no destination field
            departure_time=departure_time,
            arrival_time=arrival_time,
            is_express=False,  # no express indicator in KRIC S1 response
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
