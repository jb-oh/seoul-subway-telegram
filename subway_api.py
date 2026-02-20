"""Client for the Seoul Open Data real-time subway arrival API."""

import logging
import re
from dataclasses import dataclass

import aiohttp

from station_data import LINE_IDS

logger = logging.getLogger(__name__)

API_BASE = "http://swopenAPI.seoul.go.kr/api/subway"

# Regex for "N번째 전역" pattern in arvlMsg2
_STATION_COUNT_RE = re.compile(r"(\d+)번째\s*전역")


@dataclass
class ArrivalInfo:
    """Parsed arrival information for a single train."""

    line_name: str  # e.g. "2호선"
    station_name: str  # e.g. "강남"
    direction: str  # "상행"/"하행" or "내선"/"외선"
    destination: str  # terminal station (종착역)
    arrival_message: str  # human-readable status e.g. "전역 출발"
    arrival_seconds: int  # estimated seconds until arrival
    train_type: str  # "일반" or "급행"

    @property
    def arrival_display(self) -> str:
        if self.arrival_seconds > 0:
            minutes, seconds = divmod(self.arrival_seconds, 60)
            if minutes > 0:
                return f"{minutes}분 {seconds}초"
            return f"{seconds}초"
        return self.arrival_message

    @property
    def sort_key(self) -> int:
        if self.arrival_seconds > 0:
            return self.arrival_seconds
        return _parse_station_count(self.arrival_message) * 120


def _parse_station_count(message: str) -> int:
    """Extract approximate station distance from arvlMsg2 for sorting."""
    m = _STATION_COUNT_RE.search(message)
    if m:
        return int(m.group(1))
    if "전역 출발" in message or "전역 도착" in message:
        return 1
    if "당역 진입" in message or "도착" in message:
        return 0
    return 999


async def get_realtime_arrivals(
    api_key: str, station_name: str
) -> list[ArrivalInfo]:
    """Fetch real-time arrival data for a station.

    Args:
        api_key: Seoul Open Data API key.
        station_name: Korean station name (e.g. "강남").

    Returns:
        List of ArrivalInfo sorted by arrival time.
    """
    url = f"{API_BASE}/{api_key}/json/realtimeStationArrival/0/20/{station_name}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.error("API returned status %d", resp.status)
                    return []
                data = await resp.json(content_type=None)
    except Exception:
        logger.exception("Failed to fetch arrival data for %s", station_name)
        return []

    # Check for API error
    error_msg = data.get("errorMessage")
    if error_msg:
        status = error_msg.get("status")
        if status != 200:
            logger.error("API error: %s", error_msg.get("message", "unknown"))
            return []

    raw_list = data.get("realtimeArrivalList", [])
    results = []
    for item in raw_list:
        try:
            seconds = int(item.get("barvlDt", "0") or "0")
        except (ValueError, TypeError):
            seconds = 0

        subway_id = item.get("subwayId", "")
        info = ArrivalInfo(
            line_name=LINE_IDS.get(subway_id, subway_id),
            station_name=item.get("statnNm", station_name),
            direction=item.get("updnLine", ""),
            destination=item.get("bstatnNm", ""),
            arrival_message=item.get("arvlMsg2", ""),
            arrival_seconds=seconds,
            train_type=item.get("btrainSttus", "일반") or "일반",
        )
        results.append(info)

    results.sort(key=lambda a: a.sort_key)
    return results


def filter_by_direction(
    arrivals: list[ArrivalInfo], direction: str
) -> list[ArrivalInfo]:
    """Filter arrivals to only those matching the given direction."""
    return [a for a in arrivals if a.direction == direction]
