"""
HTML parsers for VLR.GG match detail page components.

Covers header parsing, per-map game stats, head-to-head history,
performance tab (kill matrix + advanced), and economy tab.
"""
import logging
import re

from utils.html_parsers import (
    HTMLParser,
    build_full_url,
    extract_text_content,
    normalize_image_url,
    parse_href_id_slug,
)
from utils.id_mapper import id_mapper

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Header section parsers
# ---------------------------------------------------------------------------

def _parse_event_info(html: HTMLParser) -> dict:
    """Extract event name, series, and logo from the match header."""
    event_name = ""
    event_series = ""
    event_logo = ""

    super_elem = html.css_first(".match-header-super")
    if super_elem:
        first_div = super_elem.css_first("div")
        if first_div:
            anchor = first_div.css_first("a")
            if anchor:
                event_name = extract_text_content(anchor)
            else:
                event_name = extract_text_content(first_div)

        series_elem = super_elem.css_first(".match-header-event-series")
        if series_elem:
            event_series = extract_text_content(series_elem)

    logo_elem = html.css_first(".match-header-event img")
    if logo_elem:
        src = logo_elem.attributes.get("src", "")
        event_logo = normalize_image_url(src)

    return {"name": event_name, "series": event_series, "logo": event_logo}


def _parse_match_header(html: HTMLParser) -> dict:
    """Extract date, patch, and status from the match header."""
    date = ""
    patch = ""
    status = ""

    date_elem = html.css_first(".match-header-date")
    if date_elem:
        date = extract_text_content(date_elem)

    note_elem = html.css_first(".match-header-note")
    if note_elem:
        patch = extract_text_content(note_elem)

    vs_note_elem = html.css_first(".match-header-vs-note")
    if vs_note_elem:
        status = extract_text_content(vs_note_elem)

    return {"date": date, "map_vetos": patch, "status": status}


def _is_live(html: HTMLParser) -> bool:
    """Return True if the match header indicates the match is currently LIVE."""
    vs_note_elem = html.css_first(".match-header-vs-note")
    if not vs_note_elem:
        return False
    return "LIVE" in extract_text_content(vs_note_elem).upper()


def _parse_teams(html: HTMLParser) -> list[dict]:
    """
    Extract both team entries from the match header.

    Returns a two-element list. Each entry contains name, tag, logo,
    score, and is_winner flag.
    """
    teams: list[dict] = []

    for mod in ("mod-1", "mod-2"):
        team_id = ""
        link_elem = html.css_first(f".match-header-link.{mod}")
        if link_elem:
            href = link_elem.attributes.get("href", "")
            team_id, _ = parse_href_id_slug(href)

        name_elem = html.css_first(f".match-header-link-name.{mod}")
        name = ""
        tag = ""
        if name_elem:
            full_text = name_elem.text()
            lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
            if lines:
                name = lines[0]
            if len(lines) > 1:
                tag = lines[1]

        teams.append(
            {
                "id": team_id,
                "name": name,
                "tag": tag,
                "logo": "",
                "score": "",
                "is_winner": False,
            }
        )
        id_mapper.register_team(name, team_id)

    vs_elem = html.css_first(".match-header-vs")
    if vs_elem:
        logos = vs_elem.css("img")
        for idx, img in enumerate(logos[:2]):
            src = img.attributes.get("src", "")
            if src:
                teams[idx]["logo"] = normalize_image_url(src)

    score_elems = html.css(".match-header-vs-score span")
    winner_idx = -1

    scored_spans = [
        (span.attributes.get("class") or "", span.text(strip=True))
        for span in score_elems
        if span.text(strip=True).isdigit()
    ]

    if len(scored_spans) >= 2:
        cls0, val0 = scored_spans[0]
        cls1, val1 = scored_spans[1]
        teams[0]["score"] = val0
        teams[1]["score"] = val1
        if "match-header-vs-score-winner" in cls0:
            winner_idx = 0
        elif "match-header-vs-score-winner" in cls1:
            winner_idx = 1

    if winner_idx >= 0:
        teams[winner_idx]["is_winner"] = True

    return teams


def _parse_streams_vods(html: HTMLParser) -> tuple[list[dict], list[dict]]:
    """Extract stream buttons and VOD links from the match page."""
    streams: list[dict] = []
    vods: list[dict] = []

    for btn in html.css(".match-streams-btn"):
        href = btn.attributes.get("href", "")
        name = extract_text_content(btn)
        if name or href:
            streams.append({"name": name, "url": build_full_url(href)})

    vods_container = html.css_first(".match-vods")
    if vods_container:
        for anchor in vods_container.css("a"):
            href = anchor.attributes.get("href", "")
            name = extract_text_content(anchor)
            if name or href:
                vods.append({"name": name, "url": href})

    return streams, vods


# ---------------------------------------------------------------------------
# Per-map game data parsers
# ---------------------------------------------------------------------------

def _parse_player_row(cells: list) -> dict:
    """
    Parse a single player table row into a stat dict.

    Actual VLR column layout (14 cells):
      [0]  mod-player   — player name
      [1]  mod-agents   — agent icon (img title)
      [2]  mod-stat     — rating
      [3]  mod-stat     — ACS
      [4]  mod-vlr-kills — kills
      [5]  mod-vlr-deaths — deaths
      [6]  mod-vlr-assists — assists
      [7]  mod-kd-diff  — K/D +/-
      [8]  mod-stat     — KAST
      [9]  mod-stat     — ADR
      [10] mod-stat     — HS%
      [11] mod-fb       — first kills
      [12] mod-fd       — first deaths
      [13] mod-fk-diff  — FK +/-

    Values are in .side.mod-both spans or direct text.
    """

    def cell_val(cell) -> str:
        if not cell:
            return ""
        both = cell.css_first(".side.mod-both")
        if both:
            return both.text(strip=True)
        return cell.text(strip=True)

    def safe_val(idx: int) -> str:
        return cell_val(cells[idx]) if idx < len(cells) else ""

    player_name = ""
    if cells:
        player_cell = cells[0]
        name_div = player_cell.css_first(".text-of")
        if name_div:
            player_name = name_div.text(strip=True)
        else:
            player_name = player_cell.text(strip=True)

    agent = ""
    if len(cells) > 1:
        img = cells[1].css_first("img")
        if img:
            agent = img.attributes.get("title", "") or img.attributes.get("alt", "")

    return {
        "name": player_name,
        "agent": agent,
        "rating": safe_val(2),
        "acs": safe_val(3),
        "kills": safe_val(4),
        "deaths": safe_val(5),
        "assists": safe_val(6),
        "kd_diff": safe_val(7),
        "kast": safe_val(8),
        "adr": safe_val(9),
        "hs_pct": safe_val(10),
        "fk": safe_val(11),
        "fd": safe_val(12),
        "fk_diff": safe_val(13),
    }


def _parse_map_players(game_elem) -> dict:
    """
    Parse player stat tables inside a single .vm-stats-game element.

    VLR.GG renders two back-to-back <table> blocks inside
    .vm-stats-container — one per team. We treat each table
    as one team's roster.
    """
    team1_players: list[dict] = []
    team2_players: list[dict] = []

    tables = game_elem.css("table.wf-table-inset.mod-overview")

    def parse_table_rows(table) -> list[dict]:
        players = []
        for row in table.css("tbody tr"):
            cells = row.css("td")
            if not cells:
                continue
            if len(cells) < 5:
                continue
            try:
                players.append(_parse_player_row(cells))
            except Exception as exc:
                logger.debug("Skipping player row due to parse error: %s", exc)
        return players

    if len(tables) >= 1:
        team1_players = parse_table_rows(tables[0])
    if len(tables) >= 2:
        team2_players = parse_table_rows(tables[1])

    return {"team1": team1_players, "team2": team2_players}


def _parse_map_scores(game_elem) -> dict:
    """
    Extract team scores and CT/T/OT splits from a single game header.
    """
    result = {
        "score": {"team1": "", "team2": ""},
        "score_ct": {"team1": "", "team2": ""},
        "score_t": {"team1": "", "team2": ""},
        "score_ot": {"team1": "", "team2": ""},
    }

    header = game_elem.css_first(".vm-stats-game-header")
    if not header:
        return result

    team_blocks = header.css(".team")
    keys = ["team1", "team2"]

    for idx, block in enumerate(team_blocks[:2]):
        key = keys[idx]

        score_el = block.css_first(".score")
        if score_el:
            val = score_el.text(strip=True)
            try:
                result["score"][key] = int(val)
            except (ValueError, TypeError):
                result["score"][key] = val

        ct_el = block.css_first(".mod-ct")
        if ct_el:
            result["score_ct"][key] = ct_el.text(strip=True)

        t_el = block.css_first(".mod-t")
        if t_el:
            result["score_t"][key] = t_el.text(strip=True)

        ot_el = block.css_first(".mod-ot")
        if ot_el:
            result["score_ot"][key] = ot_el.text(strip=True)

    return result


def _parse_rounds(game_elem) -> list[dict]:
    """
    Parse round-by-round outcomes from a .vlr-rounds container.
    """
    rounds: list[dict] = []
    rounds_container = game_elem.css_first(".vlr-rounds")
    if not rounds_container:
        return rounds

    round_num = 0
    for row in rounds_container.css(".vlr-rounds-row"):
        for col in row.css(".vlr-rounds-row-col"):
            cls = col.attributes.get("class", "")
            if "mod-spacing" in cls:
                continue

            sqs = col.css(".rnd-sq")
            if not sqs:
                continue

            round_num += 1

            winner = ""
            winning_side = ""
            for idx, sq in enumerate(sqs):
                sq_cls = sq.attributes.get("class", "")
                if "mod-win" in sq_cls:
                    winner = "team1" if idx == 0 else "team2"
                    if "mod-ct" in sq_cls:
                        winning_side = "ct"
                    elif "mod-t" in sq_cls:
                        winning_side = "t"
                    break

            rounds.append({
                "round_num": round_num,
                "winner": winner,
                "side": winning_side,
            })

    return rounds


def _parse_maps(html: HTMLParser) -> list[dict]:
    """Parse all per-map game blocks from the base match page."""
    maps: list[dict] = []

    for game_elem in html.css("div.vm-stats-game"):
        game_id = game_elem.attributes.get("data-game-id", "")
        if game_id == "all":
            continue

        map_name = ""
        picked_by = ""
        map_container = game_elem.css_first(".vm-stats-game-header .map")
        if map_container:
            pick_elem = map_container.css_first(".picked") or map_container.css_first(".pick")
            dur_elem = map_container.css_first(".map-duration")
            if pick_elem:
                picked_by = pick_elem.text(strip=True)
            full_text = map_container.text(strip=True)
            subtract = ""
            if pick_elem:
                subtract += pick_elem.text(strip=True)
            if dur_elem:
                subtract += dur_elem.text(strip=True)
            map_name = re.sub(r"\s+", " ", full_text.replace(subtract, "")).strip()

        duration = ""
        dur_elem = game_elem.css_first(".map-duration")
        if dur_elem:
            duration = dur_elem.text(strip=True)

        scores = _parse_map_scores(game_elem)
        players = _parse_map_players(game_elem)
        rounds = _parse_rounds(game_elem)

        maps.append({
            "map_name": map_name,
            "picked_by": picked_by,
            "duration": duration,
            "score": scores["score"],
            "score_ct": scores["score_ct"],
            "score_t": scores["score_t"],
            "score_ot": scores["score_ot"],
            "players": players,
            "rounds": rounds,
        })

    return maps


# ---------------------------------------------------------------------------
# Head-to-head history parser
# ---------------------------------------------------------------------------

def _parse_head_to_head(html: HTMLParser) -> list[dict]:
    """Parse head-to-head match history entries."""
    h2h: list[dict] = []

    container = html.css_first(".match-h2h-matches")
    if not container:
        return h2h

    for row in container.css(".wf-module-item"):
        team_elems = row.css(".match-h2h-matches-team")
        teams = []
        for te in team_elems:
            cls = te.attributes.get("class", "")
            is_winner = "mod-win" in cls
            teams.append({"name": extract_text_content(te), "is_winner": is_winner})

        score_elem = row.css_first(".match-h2h-matches-score")
        score = extract_text_content(score_elem) if score_elem else ""

        event_elem = row.css_first(".match-h2h-matches-event-name")
        event = extract_text_content(event_elem) if event_elem else ""

        date_elem = row.css_first(".match-h2h-matches-date")
        date = extract_text_content(date_elem) if date_elem else ""

        href = row.attributes.get("href", "")
        url = build_full_url(href)

        h2h.append({
            "event": event,
            "date": date,
            "teams": teams,
            "score": score,
            "url": url,
        })

    return h2h


# ---------------------------------------------------------------------------
# Performance tab parsers
# ---------------------------------------------------------------------------

def _parse_kill_matrix(html: HTMLParser) -> list[dict]:
    """
    Parse the kill matrix table from the performance tab.
    """
    matrix: list[dict] = []

    table = html.css_first("table.wf-table-inset.mod-matrix.mod-normal")
    if not table:
        return matrix

    header_row = table.css_first("thead tr")
    opponents: list[str] = []
    if header_row:
        for th in header_row.css("th"):
            opponents.append(extract_text_content(th))

    for row in table.css("tbody tr"):
        cells = row.css("td")
        if not cells:
            continue

        player_cell = cells[0]
        player_name = extract_text_content(player_cell)

        kills_vs: dict[str, str] = {}
        for idx, cell in enumerate(cells[1:], start=1):
            opponent = opponents[idx] if idx < len(opponents) else str(idx)
            kills_vs[opponent] = extract_text_content(cell)

        matrix.append({"player": player_name, "kills_vs": kills_vs})

    return matrix


def _parse_advanced_stats(html: HTMLParser) -> list[dict]:
    """
    Parse the advanced stats table from the performance tab.

    Columns: 2K, 3K, 4K, 5K, 1v1, 1v2, 1v3, 1v4, 1v5, Econ, Plants, Defuses
    """
    advanced: list[dict] = []

    table = html.css_first("table.wf-table-inset.mod-adv-stats")
    if not table:
        return advanced

    header_row = table.css_first("thead tr")
    headers: list[str] = []
    if header_row:
        for th in header_row.css("th"):
            headers.append(extract_text_content(th))

    for row in table.css("tbody tr"):
        cells = row.css("td")
        if not cells:
            continue

        player_name = extract_text_content(cells[0]) if cells else ""
        stat_dict: dict[str, str] = {"player": player_name}

        for idx, cell in enumerate(cells[1:], start=1):
            label = headers[idx] if idx < len(headers) else str(idx)
            stat_dict[label] = extract_text_content(cell)

        advanced.append(stat_dict)

    return advanced


# ---------------------------------------------------------------------------
# Economy tab parser
# ---------------------------------------------------------------------------

def _parse_economy(html: HTMLParser) -> list[dict]:
    """
    Parse the economy table from the economy tab.

    Rows per team with pistol/eco/semi-buy/full-buy win rates.
    """
    economy: list[dict] = []

    table = html.css_first("table.wf-table-inset.mod-econ")
    if not table:
        return economy

    header_row = table.css_first("thead tr")
    headers: list[str] = []
    if header_row:
        for th in header_row.css("th"):
            headers.append(extract_text_content(th))

    for row in table.css("tbody tr"):
        cells = row.css("td")
        if not cells:
            continue

        row_dict: dict[str, str] = {}
        for idx, cell in enumerate(cells):
            label = headers[idx] if idx < len(headers) else str(idx)
            row_dict[label] = extract_text_content(cell)

        economy.append(row_dict)

    return economy
