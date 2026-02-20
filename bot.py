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
)

import presets
import station_data
import subway_api

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
SEOUL_API_KEY = os.environ["SEOUL_API_KEY"]
MAX_RESULTS = 3


# ── Helpers ──────────────────────────────────────────────────────────

def format_arrivals(arrivals: list[subway_api.ArrivalInfo], limit: int = MAX_RESULTS) -> str:
    """Format a list of arrivals into a readable message."""
    if not arrivals:
        return "도착 예정 열차가 없습니다. (No upcoming trains found.)"

    lines = []
    for i, a in enumerate(arrivals[:limit], 1):
        express = " 🚄급행" if a.train_type == "급행" else ""
        if a.arrival_seconds > 0:
            time_info = f"⏱ {a.arrival_display} — {a.arrival_message}"
        else:
            time_info = f"⏱ {a.arrival_display}"
        lines.append(
            f"{i}. [{a.line_name}] {a.destination}행 ({a.direction}){express}\n"
            f"   {time_info}"
        )
    return "\n\n".join(lines)


async def query_route(departure: str, arrival: str, line: str | None = None) -> str:
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
                "(No direct line found between these stations.)\n\n"
                "역 이름을 확인해 주세요. 환승이 필요한 경우 각 구간을 별도로 조회해 주세요."
            )
        line_name, direction = result

    arrivals = await subway_api.get_realtime_arrivals(SEOUL_API_KEY, departure)
    if not arrivals:
        return f"⚠️ '{departure}'역 실시간 도착 정보를 가져올 수 없습니다."

    # Filter by direction
    filtered = subway_api.filter_by_direction(arrivals, direction)

    # If direction filter yields nothing, try filtering by line name
    if not filtered:
        filtered = [a for a in arrivals if line_name in a.line_name]

    header = f"🚇 {departure} → {arrival} ({line_name} {direction})\n\n"
    return header + format_arrivals(filtered)


# ── Command Handlers ─────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send welcome message with usage instructions."""
    text = (
        "🚇 *서울 지하철 도착 알리미* (Seoul Subway Bot)\n\n"
        "*Commands:*\n"
        "/arrivals `<역이름>` `[호선]` — 해당 역 실시간 도착 정보\n"
        "/route `<출발역>` `<도착역>` `[호선]` — 출발역→도착역 방면 다음 열차 3편\n\n"
        "*Presets:*\n"
        "/addpreset `<이름>` `<출발역>` `<도착역>` `[호선]` — 프리셋 저장\n"
        "/presets — 저장된 프리셋 목록\n"
        "/go `<이름>` — 프리셋 실행\n"
        "/delpreset `<이름>` — 프리셋 삭제\n"
        "/morning — 'morning' 프리셋 실행\n"
        "/evening — 'evening' 프리셋 실행\n\n"
        "*Examples:*\n"
        "`/arrivals 강남`\n"
        "`/arrivals 강남 신분당선`\n"
        "`/route 강남 서울역`\n"
        "`/route 강남 양재 신분당`\n"
        "`/addpreset morning 강남 양재 신분당선`\n"
        "`/morning`"
    )
    assert update.message
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_arrivals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all upcoming arrivals at a station, optionally filtered by line."""
    assert update.message
    if not context.args:
        await update.message.reply_text("사용법: /arrivals <역이름> [호선]\n예: /arrivals 강남\n예: /arrivals 강남 2호선")
        return

    station = context.args[0]
    line = None
    if len(context.args) >= 2:
        line = station_data.resolve_line(context.args[1])
        if not line:
            await update.message.reply_text(f"'{context.args[1]}' 노선을 찾을 수 없습니다.")
            return

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

    line_label = f" ({line})" if line else ""
    header = f"🚇 *{station}역*{line_label} 실시간 도착 정보\n\n"
    await update.message.reply_text(header + format_arrivals(arrivals, limit=6), parse_mode="Markdown")


async def cmd_route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show next trains from departure heading toward arrival."""
    assert update.message
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("사용법: /route <출발역> <도착역> [호선]\n예: /route 강남 서울역\n예: /route 강남 서울역 신분당선")
        return

    departure, arrival = context.args[0], context.args[1]
    line = None
    if len(context.args) >= 3:
        line = station_data.resolve_line(context.args[2])
        if not line:
            await update.message.reply_text(f"'{context.args[2]}' 노선을 찾을 수 없습니다.")
            return

    text = await query_route(departure, arrival, line)
    await update.message.reply_text(text)


async def cmd_addpreset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save a named preset route."""
    assert update.message
    if not context.args or len(context.args) < 3:
        await update.message.reply_text(
            "사용법: /addpreset <이름> <출발역> <도착역> [호선]\n"
            "예: /addpreset morning 강남 서울역\n"
            "예: /addpreset morning 강남 양재 신분당선"
        )
        return

    name, departure, arrival = context.args[0], context.args[1], context.args[2]
    line = None
    if len(context.args) >= 4:
        line = station_data.resolve_line(context.args[3])
        if not line:
            await update.message.reply_text(f"'{context.args[3]}' 노선을 찾을 수 없습니다.")
            return

    # Validate stations
    for station in (departure, arrival):
        if not station_data.get_station_lines(station):
            await update.message.reply_text(f"'{station}' 역을 찾을 수 없습니다.")
            return

    if line:
        direction = station_data.find_direction(departure, arrival, line)
        if not direction:
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

    presets.add_preset(update.message.from_user.id, name, departure, arrival, line)
    line_label = f" [{line}]" if line else ""
    await update.message.reply_text(f"✅ 프리셋 '{name}' 저장 완료: {departure} → {arrival}{line_label}")


async def cmd_presets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all saved presets."""
    assert update.message
    user_presets = presets.list_presets(update.message.from_user.id)
    if not user_presets:
        await update.message.reply_text("저장된 프리셋이 없습니다.\n/addpreset 으로 추가해 보세요.")
        return

    lines = [f"📋 *저장된 프리셋:*\n"]
    for p in user_presets:
        line_label = f" [{p.line}]" if p.line else ""
        lines.append(f"• *{p.name}*: {p.departure} → {p.arrival}{line_label}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_go(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute a saved preset."""
    assert update.message
    if not context.args:
        await update.message.reply_text("사용법: /go <프리셋이름>\n예: /go morning")
        return

    name = context.args[0]
    preset = presets.get_preset(update.message.from_user.id, name)
    if not preset:
        await update.message.reply_text(f"'{name}' 프리셋을 찾을 수 없습니다.\n/presets 로 목록을 확인하세요.")
        return

    text = await query_route(preset.departure, preset.arrival, preset.line)
    await update.message.reply_text(text)


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


async def cmd_morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shortcut for /go morning."""
    assert update.message
    preset = presets.get_preset(update.message.from_user.id, "morning")
    if not preset:
        await update.message.reply_text(
            "'morning' 프리셋이 없습니다.\n"
            "/addpreset morning <출발역> <도착역> 으로 먼저 등록해 주세요."
        )
        return
    text = await query_route(preset.departure, preset.arrival, preset.line)
    await update.message.reply_text(text)


async def cmd_evening(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shortcut for /go evening."""
    assert update.message
    preset = presets.get_preset(update.message.from_user.id, "evening")
    if not preset:
        await update.message.reply_text(
            "'evening' 프리셋이 없습니다.\n"
            "/addpreset evening <출발역> <도착역> 으로 먼저 등록해 주세요."
        )
        return
    text = await query_route(preset.departure, preset.arrival, preset.line)
    await update.message.reply_text(text)


# ── Main ─────────────────────────────────────────────────────────────

def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("arrivals", cmd_arrivals))
    app.add_handler(CommandHandler("route", cmd_route))
    app.add_handler(CommandHandler("addpreset", cmd_addpreset))
    app.add_handler(CommandHandler("presets", cmd_presets))
    app.add_handler(CommandHandler("go", cmd_go))
    app.add_handler(CommandHandler("delpreset", cmd_delpreset))
    app.add_handler(CommandHandler("morning", cmd_morning))
    app.add_handler(CommandHandler("evening", cmd_evening))

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
