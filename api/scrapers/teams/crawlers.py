"""
Team scraper orchestrators for VLR.GG.

Provides four async scraper functions:
  - vlr_team: team profile (header, rating, roster, event placements)
  - vlr_team_stats: per-map statistics
  - vlr_team_matches: paginated match history
  - vlr_team_transactions: roster transaction log
"""
import logging
import re

from fastapi import HTTPException

from utils.cache_manager import cache_manager
from utils.constants import (
    CACHE_TTL_TEAM,
    CACHE_TTL_TEAM_MATCHES,
    CACHE_TTL_TEAM_STATS,
    CACHE_TTL_TEAM_TRANSACTIONS,
    VLR_BASE_URL,
)
from utils.error_handling import handle_scraper_errors
from utils.html_parsers import parse_html
from utils.http_client import fetch_with_retries, get_http_client

from .parsers import (
    _normalize_ws,
    _parse_event_placements,
    _parse_rating_info,
    _parse_roster,
    _parse_team_header,
    _parse_team_match_item,
    _parse_transaction_item,
    _text,
)

logger = logging.getLogger(__name__)


@handle_scraper_errors
async def vlr_team_stats(team_id: str) -> dict:
    """Scrape per-map statistics from the team stats page."""
    cache_key = ("team_stats", team_id)

    async def build():
        url = f"{VLR_BASE_URL}/team/stats/{team_id}/"
        client = get_http_client()
        resp = await fetch_with_retries(url, client=client)
        status = resp.status_code

        if status >= 400:
            logger.warning("Non-200 response %d for team stats %s", status, team_id)
            raise HTTPException(
                status_code=status,
                detail=f"VLR.GG returned status {status} for team stats {team_id}",
            )

        html = parse_html(resp.text)
        rows = html.css("table.wf-table.mod-team-maps tbody tr")
        maps: list[dict] = []

        for row in rows:
            tds = row.css("td")
            if len(tds) < 13:
                continue

            map_raw = _text(tds[0])
            m = re.match(r"(.+?)\s*\((\d+)\)", map_raw)
            map_name = m.group(1).strip() if m else map_raw
            map_games = m.group(2) if m else ""

            maps.append({
                "map": _normalize_ws(map_name),
                "games": int(map_games) if map_games.isdigit() else 0,
                "win_pct": _text(tds[2]),
                "wins": int(_text(tds[3])) if _text(tds[3]).isdigit() else 0,
                "losses": int(_text(tds[4])) if _text(tds[4]).isdigit() else 0,
                "atk_first": int(_text(tds[5])) if _text(tds[5]).isdigit() else 0,
                "def_first": int(_text(tds[6])) if _text(tds[6]).isdigit() else 0,
                "atk_rwin_pct": _text(tds[7]),
                "atk_rw": int(_text(tds[8])) if _text(tds[8]).isdigit() else 0,
                "atk_rl": int(_text(tds[9])) if _text(tds[9]).isdigit() else 0,
                "def_rwin_pct": _text(tds[10]),
                "def_rw": int(_text(tds[11])) if _text(tds[11]).isdigit() else 0,
                "def_rl": int(_text(tds[12])) if _text(tds[12]).isdigit() else 0,
            })

        return {"data": {"status": status, "segments": maps}}

    return await cache_manager.get_or_create_async(CACHE_TTL_TEAM_STATS, build, *cache_key)


@handle_scraper_errors
async def vlr_team(team_id: str) -> dict:
    """Scrape a team profile page from VLR.GG."""
    cache_key = ("team", team_id)

    async def build():
        url = f"{VLR_BASE_URL}/team/{team_id}"
        client = get_http_client()
        resp = await fetch_with_retries(url, client=client)
        status = resp.status_code

        if status >= 400:
            logger.warning("Non-200 response %d for team %s", status, team_id)
            raise HTTPException(
                status_code=status,
                detail=f"VLR.GG returned status {status} for team {team_id}",
            )

        html = parse_html(resp.text)

        header_info = _parse_team_header(html, team_id)
        rating = _parse_rating_info(html)
        roster = _parse_roster(html)
        event_placements, total_winnings = _parse_event_placements(html)

        segment = {
            **header_info,
            "rating": rating,
            "roster": roster,
            "event_placements": event_placements,
            "total_winnings": total_winnings,
        }

        return {"data": {"status": status, "segments": [segment]}}

    return await cache_manager.get_or_create_async(CACHE_TTL_TEAM, build, *cache_key)


@handle_scraper_errors
async def vlr_team_matches(team_id: str, page: int = 1) -> dict:
    """Scrape the paginated match history for a team from VLR.GG."""
    page = max(1, min(page, 100))
    cache_key = ("team_matches", team_id, page)

    async def build():
        url = f"{VLR_BASE_URL}/team/matches/{team_id}/?page={page}"
        client = get_http_client()
        resp = await fetch_with_retries(url, client=client)
        status = resp.status_code

        if status >= 400:
            logger.warning(
                "Non-200 response %d for team matches %s page %d", status, team_id, page
            )
            raise HTTPException(
                status_code=status,
                detail=(
                    f"VLR.GG returned status {status} for team matches "
                    f"{team_id} page {page}"
                ),
            )

        html = parse_html(resp.text)
        matches: list[dict] = []

        selectors = ["a.wf-card.m-item", ".wf-card a.m-item", "a.m-item"]
        items = []
        for sel in selectors:
            items = html.css(sel)
            if items:
                break

        if not items:
            content = html.css_first(".col.mod-1") or html.css_first(".col")
            if content:
                items = content.css("a[href*='/match/']") or content.css("a[href*='/matches/']")

        for item in items:
            try:
                parsed = _parse_team_match_item(item)
                if parsed is not None:
                    matches.append(parsed)
            except Exception as exc:
                logger.warning("Failed to parse match item for team %s: %s", team_id, exc)

        return {
            "data": {
                "status": status,
                "segments": matches,
                "meta": {"page": page},
            }
        }

    return await cache_manager.get_or_create_async(CACHE_TTL_TEAM_MATCHES, build, *cache_key)


@handle_scraper_errors
async def vlr_team_transactions(team_id: str) -> dict:
    """Scrape the roster transaction history for a team from VLR.GG."""
    cache_key = ("team_transactions", team_id)

    async def build():
        url = f"{VLR_BASE_URL}/team/transactions/{team_id}/"
        client = get_http_client()
        resp = await fetch_with_retries(url, client=client)
        status = resp.status_code

        if status >= 400:
            logger.warning("Non-200 response %d for team transactions %s", status, team_id)
            raise HTTPException(
                status_code=status,
                detail=f"VLR.GG returned status {status} for team transactions {team_id}",
            )

        html = parse_html(resp.text)
        transactions: list[dict] = []

        selectors = ["tr.txn-item", ".txn-item", ".wf-card .txn-item"]
        items = []
        for sel in selectors:
            items = html.css(sel)
            if items:
                break

        if not items:
            items = [
                row
                for row in html.css("tr")
                if row.css_first("a[href*='/player/']")
            ]

        if not items:
            content = html.css_first(".col.mod-1") or html.css_first(".col")
            if content:
                items = [
                    card
                    for card in content.css(".wf-card")
                    if card.css_first("a[href*='/player/']")
                ]

        for item in items:
            try:
                parsed = _parse_transaction_item(item)
                if parsed is not None:
                    transactions.append(parsed)
            except Exception as exc:
                logger.warning("Failed to parse transaction item for team %s: %s", team_id, exc)

        return {"data": {"status": status, "segments": transactions}}

    return await cache_manager.get_or_create_async(CACHE_TTL_TEAM_TRANSACTIONS, build, *cache_key)
