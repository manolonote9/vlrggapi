import logging

from utils.cache_manager import cache_manager
from utils.constants import CACHE_TTL_STATS, VLR_STATS_URL
from utils.error_handling import (
    handle_scraper_errors,
    raise_for_upstream_status,
    validate_region,
    validate_timespan,
)
from utils.html_parsers import extract_text_content, parse_html
from utils.http_client import fetch_with_retries, get_http_client

logger = logging.getLogger(__name__)


def _cell_text(cells: list, index: int) -> str:
    """Read a table cell by index without raising on sparse rows."""
    if index >= len(cells):
        return ""
    return extract_text_content(cells[index])


_STATS_FIELD_MAP = {
    "rnd": "rounds_played",
    "rating2": "rating",
    "acs": "average_combat_score",
    "kd": "kill_deaths",
    "kast": "kill_assists_survived_traded",
    "adr": "average_damage_per_round",
    "kpr": "kills_per_round",
    "apr": "assists_per_round",
    "fbpr": "first_kills_per_round",
    "fdpr": "first_deaths_per_round",
    "hsp": "headshot_percentage",
    "clp": "clutch_success_percentage",
    "cl": "clutch_attempts",
}

REQUIRED_STATS_KEYS = frozenset({"rnd", "rating2", "acs", "kd", "adr"})

_LEGACY_STATS_INDICES = {
    "rounds_played": 2,
    "rating": 3,
    "average_combat_score": 4,
    "kill_deaths": 5,
    "kill_assists_survived_traded": 6,
    "average_damage_per_round": 7,
    "kills_per_round": 8,
    "assists_per_round": 9,
    "first_kills_per_round": 10,
    "first_deaths_per_round": 11,
    "headshot_percentage": 12,
    "clutch_success_percentage": 13,
    "clutch_attempts": 14,
}


def _build_column_map(html) -> dict | None:
    header = html.css_first("thead tr")
    if header is None:
        return None
    col_map: dict[str, int] = {}
    for index, th in enumerate(header.css("th")):
        data_col = th.attributes.get("data-col")
        if data_col:
            col_map[data_col] = index
    return col_map or None


def _cell_text(cells: list, index: int | None) -> str:
    if index is None or index >= len(cells):
        return ""
    return extract_text_content(cells[index])


def _parse_stats_row(item, col_map: dict | None = None) -> dict:
    cells = item.css("td")
    player_cell = item.css_first("td.mod-player")

    player_name = extract_text_content(player_cell.css_first(".text-of")) if player_cell else ""
    org_cell = None
    if player_cell:
        org_cell = (
            player_cell.css_first(".st-pl-country")
            or player_cell.css_first(".stats-player-country")
        )
    org = extract_text_content(org_cell) if org_cell else ""
    if not org:
        org = "N/A"

    agents = []
    for agent_img in item.css("td.mod-agents img"):
        src = agent_img.attributes.get("src", "")
        if not src:
            continue
        agents.append(src.split("/")[-1].split(".")[0])

    row = {"player": player_name, "org": org, "agents": agents}
    if col_map is not None:
        for data_col, out_key in _STATS_FIELD_MAP.items():
            row[out_key] = _cell_text(cells, col_map.get(data_col))
    else:
        for out_key, index in _LEGACY_STATS_INDICES.items():
            row[out_key] = _cell_text(cells, index)
    return row


@handle_scraper_errors
async def vlr_stats(region_key: str, timespan: str, event_id: str | None = None):
    async def build():
        validate_region(region_key)
        validate_timespan(timespan)

        event_id_param = event_id if event_id else "all"
        base_url = (
            f"{VLR_STATS_URL}/?event_group_id=all&event_id={event_id_param}"
            f"&region={region_key}&country=all&min_rounds=200"
            f"&min_rating=1550&agent=all&map_id=all"
        )
        url = (
            f"{base_url}&timespan=all"
            if timespan.lower() == "all"
            else f"{base_url}&timespan={timespan}d"
        )

        client = get_http_client()
        resp = await fetch_with_retries(url, client=client)
        status = resp.status_code
        raise_for_upstream_status(status, "stats")

        html = parse_html(resp.text)

        col_map = _build_column_map(html)

        result = []
        for item in html.css("tbody tr"):
            parsed = _parse_stats_row(item, col_map)
            if parsed["player"]:
                result.append(parsed)

        data = {"data": {"status": status, "segments": result}}

        return data

    return await cache_manager.get_or_create_async(
        CACHE_TTL_STATS, build, "stats", region_key, timespan, event_id or "all"
    )
