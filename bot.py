"""Seoul Subway Telegram Bot.

Provides real-time subway arrival information for Seoul Metro stations.
Supports ad-hoc queries and pre-configured commute presets.
"""

import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import presets
import station_data
import subway_api
import timetable_api

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
SEOUL_API_KEY = os.environ["SEOUL_API_KEY"]
KRIC_API_KEY = os.environ.get("KRIC_API_KEY", "")
MAX_RESULTS = 3


# ── Helpers ──────────────────────────────────────────────────────────

_DIRECTIONS = {"상행", "하행", "내선", "외선"}


def _parse_filter_args(args: list[str]) -> tuple[str | None, str | None, str | None]:
    """Parse optional [호선] [방향] [종착역] filter args.

    Returns:
        (line, direction, destination) — each None if not provided.
    """
    line = None
    direction = None
    destination = None

    for arg in args:
        resolved = station_data.resolve_line(arg)
        if resolved:
            line = resolved
        elif arg in _DIRECTIONS:
            direction = arg
        else:
            # Treat as destination; strip trailing 행 if result is non-empty
            dest = arg
            if dest.endswith("행") and len(dest) > 1:
                dest = dest[:-1]
            destination = dest

    return line, direction, destination


def format_arrivals(arrivals: list[subway_api.ArrivalInfo], limit: int = MAX_RESULTS) -> str:
    """Format a list of arrivals into a readable message."""
    if not arrivals:
        return "도착 예정 열차가 없습니다."

    lines = []
    for i, a in enumerate(arrivals[:limit], 1):
        express = " 🚄급행" if a.train_type == "급행" else ""
        if a.arrival_seconds > 0:
            if a.arrival_message.endswith("후"):
                time_info = f"⏱ {a.arrival_display}"
            else:
                time_info = f"⏱ {a.arrival_display} — {a.arrival_message}"
        else:
            time_info = f"⏱ {a.arrival_display}"
        lines.append(
            f"{i}. [{a.line_name}] {a.destination}행 ({a.direction}){express}\n"
            f"   {time_info}"
        )
    return "\n\n".join(lines)


async def query_route(
    departure: str,
    arrival: str,
    line: str | None = None,
    dir_override: str | None = None,
    dest_override: str | None = None,
) -> str:
    """Query arrivals from departure toward arrival and return formatted text."""
    if line:
        direction = station_data.find_direction(departure, arrival, line)
        if not direction:
            return (
                f"❌ '{departure}'과(와) '{arrival}'은(는) {line}에서 찾을 수 없습니다."
            )
        line_name = line
    else:
        result = station_data.find_common_line(departure, arrival)
        if not result:
            return (
                f"❌ '{departure}'과(와) '{arrival}' 사이의 직통 노선을 찾을 수 없습니다.\n"
                "역 이름을 확인해 주세요. 환승이 필요한 경우 각 구간을 별도로 조회해 주세요."
            )
        line_name, direction = result

    # Apply direction override if provided
    if dir_override:
        direction = dir_override

    arrivals = await subway_api.get_realtime_arrivals(SEOUL_API_KEY, departure)
    if not arrivals:
        return f"⚠️ '{departure}'역 실시간 도착 정보를 가져올 수 없습니다."

    # Filter by line first, then by direction
    filtered = [a for a in arrivals if line_name in a.line_name]
    dir_filtered = subway_api.filter_by_direction(filtered, direction)
    if dir_filtered:
        filtered = dir_filtered

    # Filter by destination override
    if dest_override:
        filtered = [a for a in filtered if a.destination == dest_override]

    # Filter out trains that terminate before the arrival station
    filtered = [
        a for a in filtered
        if station_data.train_reaches_station(line_name, arrival, a.destination, direction)
    ]

    header = f"🚇 {departure} → {arrival} ({line_name} {direction})\n\n"
    return header + format_arrivals(filtered)


# ── Command Handlers ─────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send welcome message with usage instructions."""
    text = (
        "🚇 *서울 지하철 도착 알리미*\n\n"
        "*명령어:*\n"
        "/arrivals `<역이름>` `[호선]` `[상행/하행]` `[종착역행]`\n"
        "  해당 역 실시간 도착 정보 (방향/종착역 필터 가능)\n"
        "/route `<출발역>` `<도착역>` `[호선]` `[상행/하행]` `[종착역행]`\n"
        "  출발역→도착역 방면 다음 열차 3편\n"
        "/timetable `<역이름>` `[호선]` `[상행/하행]`\n"
        "  역 시간표 조회 — 1·2·3·4·6·7·8·9호선 지원\n\n"
        "*프리셋:*\n"
        "/addpreset `<이름>` `<출발역>` `<도착역>` `[호선]` `[상행/하행]` `[종착역행]`\n"
        "/presets — 저장된 프리셋 목록\n"
        "/delpreset `<이름>` — 프리셋 삭제\n"
        "`/<이름>` — 저장된 프리셋 실행\n\n"
        "*사용 예시:*\n"
        "`/arrivals 강남`\n"
        "`/arrivals 강남 4호선 상행`\n"
        "`/route 강남 서울역`\n"
        "`/timetable 교대 3호선`\n"
        "`/addpreset 출근 정자 강남 수인분당선 상행`\n"
        "`/출근`"
    )
    assert update.message
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_arrivals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all upcoming arrivals at a station, optionally filtered."""
    assert update.message
    if not context.args:
        await update.message.reply_text(
            "사용법: /arrivals <역이름> [호선] [상행/하행] [종착역행]\n"
            "예: /arrivals 강남\n"
            "예: /arrivals 강남 4호선 상행\n"
            "예: /arrivals 강남 4호선 당고개행"
        )
        return

    station = station_data.normalize_station_name(context.args[0])
    line, direction, destination = _parse_filter_args(context.args[1:])

    # Validate station exists in our data
    known_lines = station_data.get_station_lines(station)
    if not known_lines:
        suggestions = station_data.search_station(station)
        if suggestions:
            await update.message.reply_text(
                f"'{station}' 역을 찾을 수 없습니다. 혹시 이 역을 찾으셨나요?\n"
                + ", ".join(suggestions[:10])
            )
        else:
            await update.message.reply_text(f"'{station}' 역을 찾을 수 없습니다.")
        return

    if line and line not in known_lines:
        await update.message.reply_text(
            f"'{station}'역은 {line}에 없습니다.\n"
            f"이용 가능 노선: {', '.join(known_lines)}"
        )
        return

    arrivals = await subway_api.get_realtime_arrivals(SEOUL_API_KEY, station)
    if not arrivals:
        await update.message.reply_text(f"⚠️ '{station}'역 실시간 도착 정보를 가져올 수 없습니다.")
        return

    if line:
        arrivals = [a for a in arrivals if line in a.line_name]
    if direction:
        arrivals = [a for a in arrivals if a.direction == direction]
    if destination:
        arrivals = [a for a in arrivals if a.destination == destination]

    filter_parts = [f for f in (line, direction, f"{destination}행" if destination else None) if f]
    filter_label = f" ({' '.join(filter_parts)})" if filter_parts else ""
    header = f"🚇 *{station}역*{filter_label} 실시간 도착 정보\n\n"
    await update.message.reply_text(header + format_arrivals(arrivals, limit=6), parse_mode="Markdown")


async def cmd_route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show next trains from departure heading toward arrival."""
    assert update.message
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "사용법: /route <출발역> <도착역> [호선] [상행/하행] [종착역행]\n"
            "예: /route 강남 서울역\n"
            "예: /route 강남 서울역 4호선 당고개행"
        )
        return

    departure = station_data.normalize_station_name(context.args[0])
    arrival = station_data.normalize_station_name(context.args[1])
    line, direction, destination = _parse_filter_args(context.args[2:])

    text = await query_route(departure, arrival, line, dir_override=direction, dest_override=destination)
    await update.message.reply_text(text)


async def cmd_timetable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show scheduled timetable for a station (first/last train + upcoming)."""
    assert update.message
    if not context.args:
        await update.message.reply_text(
            "사용법: /timetable <역이름> [호선] [상행/하행]\n"
            "예: /timetable 교대 3호선\n"
            "예: /timetable 당산 9호선 상행\n"
            "예: /timetable 정자 수인분당선\n\n"
            "ℹ️ 1·2·3·4·6·7·8·9호선 지원 (수도권 광역철도는 준비 중)"
        )
        return

    station = station_data.normalize_station_name(context.args[0])
    line, direction, _ = _parse_filter_args(context.args[1:])

    if line and line not in timetable_api.ALL_SUPPORTED_LINES:
        await update.message.reply_text(
            f"'{line}'은(는) 시간표 조회가 지원되지 않습니다.\n"
            "3·4·6·7·8·9호선, 수인분당선, 경의중앙선, 경춘선, 공항철도, 신분당선 등 지원합니다."
        )
        return

    weekday_code, weekday_label = timetable_api.get_weekday_type()

    # ── KRIC lines (1·2호선 via S1, 수인분당선 etc. future) ─────────────
    if line in timetable_api.KRIC_LINES:
        if not KRIC_API_KEY:
            await update.message.reply_text(
                f"'{line}' 시간표 서비스는 현재 준비 중입니다.\n"
                "잠시 후 다시 시도해 주세요."
            )
            return

        kric_code = timetable_api.get_station_kric_code(station, line)
        if not kric_code:
            if line not in timetable_api.KRIC_S1_LINES:
                await update.message.reply_text(
                    f"'{line}' 시간표는 현재 준비 중입니다.\n"
                    "수도권 광역철도(수인분당선, 경의중앙선, 공항철도 등)는 "
                    "향후 지원될 예정입니다."
                )
            else:
                await update.message.reply_text(
                    f"'{station}'역 {line} 시간표 코드를 찾을 수 없습니다.\n"
                    "역 이름을 확인해 주세요."
                )
            return

        resolved_line = line
        parts = [f"🕐 {station}역 시간표 ({resolved_line}, {weekday_label})\n"]

        if line in timetable_api.KRIC_S1_LINES:
            # S1 API returns all trains without direction/destination info.
            timetable = await timetable_api.get_timetable_kric(
                KRIC_API_KEY, resolved_line, kric_code, weekday_code, 1
            )
            dir_label = "순환" if line == "2호선" else "전방향"
            _append_timetable_section(parts, dir_label, timetable)
            parts.append("\nℹ️ 방향/목적지 정보는 제공되지 않습니다.")
        else:
            if direction:
                directions = [(timetable_api.direction_to_code(direction), direction)]
            else:
                label_1 = timetable_api.direction_code_to_label(1, resolved_line)
                label_2 = timetable_api.direction_code_to_label(2, resolved_line)
                directions = [(1, label_1), (2, label_2)]
            for dir_code, dir_label in directions:
                timetable = await timetable_api.get_timetable_kric(
                    KRIC_API_KEY, resolved_line, kric_code, weekday_code, dir_code
                )
                _append_timetable_section(parts, dir_label, timetable)

        await update.message.reply_text("\n".join(parts))
        return

    # ── Seoul Metro lines (3·4·6·7·8·9호선) via FR_CODE service ─────────
    result = await timetable_api.get_station_fr_code(SEOUL_API_KEY, station, line)
    if not result:
        await update.message.reply_text(
            f"'{station}'역 시간표를 찾을 수 없습니다.\n"
            "역 이름을 확인하거나, 호선을 함께 입력해 주세요."
        )
        return

    fr_code, resolved_line = result

    if resolved_line not in timetable_api.SUPPORTED_LINES:
        # Seoul API resolved a KRIC-served line (e.g. 1호선 at 서울역) — redirect.
        if resolved_line in timetable_api.KRIC_S1_LINES and KRIC_API_KEY:
            kric_code = timetable_api.get_station_kric_code(station, resolved_line)
            if kric_code:
                parts = [f"🕐 {station}역 시간표 ({resolved_line}, {weekday_label})\n"]
                timetable = await timetable_api.get_timetable_kric(
                    KRIC_API_KEY, resolved_line, kric_code, weekday_code, 1
                )
                dir_label = "순환" if resolved_line == "2호선" else "전방향"
                _append_timetable_section(parts, dir_label, timetable)
                parts.append("\nℹ️ 방향/목적지 정보는 제공되지 않습니다.")
                await update.message.reply_text("\n".join(parts))
                return
        await update.message.reply_text(
            f"'{resolved_line}'은(는) 시간표 조회가 지원되지 않습니다.\n"
            "1·2·3·4·6·7·8·9호선을 지원합니다."
        )
        return

    if direction:
        directions = [(timetable_api.direction_to_code(direction), direction)]
    else:
        label_1 = timetable_api.direction_code_to_label(1, resolved_line)
        label_2 = timetable_api.direction_code_to_label(2, resolved_line)
        directions = [(1, label_1), (2, label_2)]

    parts = [f"🕐 {station}역 시간표 ({resolved_line}, {weekday_label})\n"]
    for dir_code, dir_label in directions:
        timetable = await timetable_api.get_timetable(
            SEOUL_API_KEY, fr_code, weekday_code, dir_code
        )
        _append_timetable_section(parts, dir_label, timetable)

    await update.message.reply_text("\n".join(parts))


def _append_timetable_section(
    parts: list[str], dir_label: str, timetable: list[timetable_api.TimetableEntry]
) -> None:
    """Append a formatted direction section to parts in-place."""
    if not timetable:
        parts.append(f"\n📌 {dir_label}: 시간표 정보 없음\n")
        return

    first, last = timetable_api.get_first_last(timetable)
    upcoming = timetable_api.get_upcoming(timetable, count=5)

    parts.append(f"\n📌 {dir_label}")
    if first and last:
        def _dest(entry: timetable_api.TimetableEntry) -> str:
            return f" ({entry.destination}행)" if entry.destination else ""
        parts.append(
            f"  첫차: {first.departure_display}{_dest(first)}"
            f" / 막차: {last.departure_display}{_dest(last)}"
        )
    if upcoming:
        parts.append("  ⏭ 다음 열차:")
        for i, entry in enumerate(upcoming, 1):
            express = " 🚄급행" if entry.is_express else ""
            dest = f" → {entry.destination}행" if entry.destination else ""
            trn = f" ({entry.train_no})" if entry.train_no else ""
            parts.append(f"  {i}. {entry.departure_display}{trn}{dest}{express}")
    else:
        parts.append("  금일 운행이 종료되었습니다.")


async def cmd_addpreset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save a named preset route."""
    assert update.message
    if not context.args or len(context.args) < 3:
        await update.message.reply_text(
            "사용법: /addpreset <이름> <출발역> <도착역> [호선] [상행/하행] [종착역행]\n"
            "예: /addpreset 출근 강남 서울역\n"
            "예: /addpreset 출근 정자 강남 수인분당선 상행"
        )
        return

    name = context.args[0]
    departure = station_data.normalize_station_name(context.args[1])
    arrival = station_data.normalize_station_name(context.args[2])
    line, direction, destination = _parse_filter_args(context.args[3:])

    # Validate stations
    for station in (departure, arrival):
        if not station_data.get_station_lines(station):
            await update.message.reply_text(f"'{station}' 역을 찾을 수 없습니다.")
            return

    if line:
        inferred_dir = station_data.find_direction(departure, arrival, line)
        if not inferred_dir:
            await update.message.reply_text(
                f"'{departure}'과(와) '{arrival}'은(는) {line}에서 찾을 수 없습니다."
            )
            return
    else:
        if not station_data.find_common_line(departure, arrival):
            await update.message.reply_text(
                f"'{departure}'과(와) '{arrival}' 사이의 직통 노선을 찾을 수 없습니다."
            )
            return

    presets.add_preset(
        update.message.from_user.id, name, departure, arrival,
        line=line, direction=direction, destination=destination,
    )
    extras = []
    if line:
        extras.append(line)
    if direction:
        extras.append(direction)
    if destination:
        extras.append(f"{destination}행")
    extras_label = f" [{' '.join(extras)}]" if extras else ""
    await update.message.reply_text(f"✅ 프리셋 '{name}' 저장 완료: {departure} → {arrival}{extras_label}")


async def cmd_presets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all saved presets."""
    assert update.message
    user_presets = presets.list_presets(update.message.from_user.id)
    if not user_presets:
        await update.message.reply_text("저장된 프리셋이 없습니다.\n/addpreset 으로 추가해 보세요.")
        return

    lines = [f"📋 *저장된 프리셋:*\n"]
    for p in user_presets:
        extras = []
        if p.line:
            extras.append(p.line)
        if p.direction:
            extras.append(p.direction)
        if p.destination:
            extras.append(f"{p.destination}행")
        extras_label = f" [{' '.join(extras)}]" if extras else ""
        lines.append(f"• *{p.name}*: {p.departure} → {p.arrival}{extras_label}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_delpreset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete a saved preset."""
    assert update.message
    if not context.args:
        await update.message.reply_text("사용법: /delpreset <프리셋이름>")
        return

    name = context.args[0]
    if presets.delete_preset(update.message.from_user.id, name):
        await update.message.reply_text(f"🗑 프리셋 '{name}' 삭제 완료.")
    else:
        await update.message.reply_text(f"'{name}' 프리셋을 찾을 수 없습니다.")


async def cmd_preset_shortcut(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /<preset_name> as a shortcut to run a saved preset."""
    assert update.message and update.message.text
    name = update.message.text.split()[0].lstrip("/")
    preset = presets.get_preset(update.message.from_user.id, name)
    if not preset:
        return

    text = await query_route(
        preset.departure, preset.arrival, preset.line,
        dir_override=preset.direction, dest_override=preset.destination,
    )
    await update.message.reply_text(text)


# ── Main ─────────────────────────────────────────────────────────────

def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("arrivals", cmd_arrivals))
    app.add_handler(CommandHandler("route", cmd_route))
    app.add_handler(CommandHandler("timetable", cmd_timetable))
    app.add_handler(CommandHandler("addpreset", cmd_addpreset))
    app.add_handler(CommandHandler("presets", cmd_presets))
    app.add_handler(CommandHandler("delpreset", cmd_delpreset))
    # Catch-all: any unrecognized /command (including Korean) is treated as a preset name
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^/"), cmd_preset_shortcut,
    ))

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
