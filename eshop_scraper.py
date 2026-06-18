#!/usr/bin/env python3
"""
HTML scrapers for Nintendo eShop price checks.

The default scraper reads public HTML from eShop-Prices game pages. It does not
call Nintendo's price API. The page's normalized sort value is EUR cents, so VND
conversion uses a separate free FX feed at scrape time.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36"
)
ESHOP_PRICES_BASE_URL = "https://eshop-prices.com"
FX_URL = "https://open.er-api.com/v6/latest/EUR"

REGION_NAMES = {
    "AR": "Argentina",
    "AT": "Austria",
    "AU": "Australia",
    "BE": "Belgium",
    "BG": "Bulgaria",
    "BR": "Brazil",
    "CA": "Canada",
    "CH": "Switzerland",
    "CL": "Chile",
    "CO": "Colombia",
    "CY": "Cyprus",
    "CZ": "Czech Republic",
    "DE": "Germany",
    "DK": "Denmark",
    "EE": "Estonia",
    "ES": "Spain",
    "FI": "Finland",
    "FR": "France",
    "GB": "United Kingdom",
    "GR": "Greece",
    "HK": "Hong Kong",
    "HR": "Croatia",
    "HU": "Hungary",
    "IE": "Ireland",
    "IL": "Israel",
    "IT": "Italy",
    "JP": "Japan",
    "KR": "South Korea",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "MT": "Malta",
    "MX": "Mexico",
    "MY": "Malaysia",
    "NL": "Netherlands",
    "NO": "Norway",
    "NZ": "New Zealand",
    "PE": "Peru",
    "PH": "Philippines",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "SE": "Sweden",
    "SG": "Singapore",
    "SI": "Slovenia",
    "SK": "Slovakia",
    "TH": "Thailand",
    "TW": "Taiwan",
    "US": "United States",
    "ZA": "South Africa",
}


@dataclass(frozen=True)
class GameConfig:
    key: str
    name: str
    eshop_prices_url: str


@dataclass
class RegionPrice:
    code: str
    country: str
    price_display: str | None
    regular_display: str | None
    eur_value: float | None
    vnd_value: float | None
    sale_note: str
    buy_url: str | None
    available: bool
    note: str = ""


@dataclass
class ScrapeReport:
    game_key: str
    game_name: str
    source: str
    source_url: str
    generated_at_utc: str
    fx_updated_utc: str
    eur_to_vnd: float
    results: list[RegionPrice]

    @property
    def comparable(self) -> list[RegionPrice]:
        rows = [row for row in self.results if row.vnd_value is not None]
        return sorted(rows, key=lambda row: row.vnd_value or float("inf"))

    @property
    def missing(self) -> list[RegionPrice]:
        return [row for row in self.results if row.vnd_value is None]

    @property
    def lowest(self) -> RegionPrice | None:
        rows = self.comparable
        return rows[0] if rows else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_key": self.game_key,
            "game_name": self.game_name,
            "source": self.source,
            "source_url": self.source_url,
            "generated_at_utc": self.generated_at_utc,
            "fx_updated_utc": self.fx_updated_utc,
            "eur_to_vnd": self.eur_to_vnd,
            "results": [asdict(row) for row in self.results],
        }


def fetch_text(url: str, timeout: int = 35) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def fetch_json(url: str, timeout: int = 25) -> Any:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def clean_html(raw: str) -> str:
    raw = re.sub(r"<script\b.*?</script>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<style\b.*?</style>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<svg\b.*?</svg>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return " ".join(html.unescape(raw).split())


def vnd(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{round(value):,}".replace(",", ".") + " ₫"


def eur(value: float | None) -> str:
    if value is None:
        return "-"
    return f"EUR {value:.2f}"


def load_games(path: str) -> dict[str, GameConfig]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    games = {}
    for key, item in data.items():
        games[key] = GameConfig(
            key=key,
            name=item["name"],
            eshop_prices_url=item["eshop_prices_url"],
        )
    return games


def games_file_path(path: str | None = None) -> str:
    if path:
        return path
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "games.json")


def resolve_game(game_arg: str, games: dict[str, GameConfig]) -> GameConfig:
    if game_arg in games:
        return games[game_arg]

    normalized = game_arg.strip()
    for game in games.values():
        if normalized.lower() == game.name.lower():
            return game

    if normalized.startswith("https://eshop-prices.com/games/"):
        match = re.search(r"/games/\d+-([^/?#]+)", normalized)
        key = match.group(1) if match else "custom"
        parts = urlsplit(normalized)
        clean_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        return GameConfig(key=key, name=key.replace("-", " ").title(), eshop_prices_url=clean_url)

    result = search_game(normalized)
    if result:
        return result

    available = ", ".join(sorted(games))
    raise ValueError(
        f"Could not find '{game_arg}' on eShop-Prices HTML search. "
        f"Configured shortcuts: {available}"
    )


def search_game(query: str) -> GameConfig | None:
    if not query:
        return None

    for search_query in search_query_variants(query):
        matches = search_game_once(search_query)
        if not matches:
            continue

        lowered = search_query.casefold()
        for game in matches:
            if game.name.casefold() == lowered:
                return game
        return matches[0]

    return None


def search_query_variants(query: str) -> list[str]:
    variants = [query.strip()]
    spaced_digits = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", query.strip())
    if spaced_digits not in variants:
        variants.append(spaced_digits)

    compact_aliases = {
        "megaman": "mega man",
        "pokemon": "pokemon",
    }
    alias = compact_aliases.get(re.sub(r"\s+", "", query).casefold())
    if alias and alias not in variants:
        variants.append(alias)

    return [variant for variant in variants if variant]


def search_game_once(query: str) -> list[GameConfig]:
    url = f"{ESHOP_PRICES_BASE_URL}/games?q={quote_plus(query)}"
    page_html = fetch_text(url)
    matches: list[GameConfig] = []

    for match in re.finditer(
        r'<a\s+class="games-list-item"\s+href="(/games/\d+-[^"]+)">(.*?)</a>',
        page_html,
        re.S | re.I,
    ):
        href, body = match.groups()
        title_match = re.search(r"<h5>(.*?)</h5>", body, re.S | re.I)
        title = clean_html(title_match.group(1)) if title_match else clean_html(body)
        if not title:
            continue
        game_url = urljoin(ESHOP_PRICES_BASE_URL, html.unescape(href))
        key_match = re.search(r"/games/\d+-([^/?#]+)", game_url)
        key = key_match.group(1) if key_match else title.lower().replace(" ", "-")
        matches.append(GameConfig(key=key, name=title, eshop_prices_url=game_url))

    return matches


def get_fx() -> tuple[str, float]:
    data = fetch_json(FX_URL)
    if data.get("result") != "success":
        raise RuntimeError(f"FX request failed: {data}")
    rates = data.get("rates") or {}
    if "VND" not in rates:
        raise RuntimeError("FX feed did not include VND")
    return data.get("time_last_update_utc", "unknown"), float(rates["VND"])


def extract_game_name(page_html: str, fallback: str) -> str:
    match = re.search(r'"@type"\s*:\s*"VideoGame".*?"name"\s*:\s*"([^"]+)"', page_html, re.S)
    if match:
        return html.unescape(match.group(1)).strip()
    match = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', page_html, re.I)
    if match:
        title = html.unescape(match.group(1)).strip()
        return re.sub(r"\s+\|\s+Cheapest eShop Price\s*$", "", title, flags=re.I)
    return fallback


def extract_available_codes(page_html: str) -> list[str]:
    match = re.search(r"window\.AVAILABLE_COUNTRIES\s*=\s*(\[.*?\])", page_html)
    if not match:
        return []
    try:
        codes = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    return [str(code).upper() for code in codes]


def parse_price_row(row_html: str, eur_to_vnd: float) -> RegionPrice | None:
    code_match = re.search(r"#i-flag-([a-z]{2})", row_html)
    sort_match = re.search(r'data-sort-with="([0-9]+)"', row_html)
    if not code_match or not sort_match:
        return None

    code = code_match.group(1).upper()
    cells = re.findall(r"<td(?:\s[^>]*)?>(.*?)</td>", row_html, re.S | re.I)
    if len(cells) < 4:
        return None

    country = clean_html(cells[1]) or REGION_NAMES.get(code, code)
    value_html = cells[3]
    price_display = clean_html(value_html)
    regular_display = None

    regular_match = re.search(r"<del>(.*?)</del>", value_html, re.S | re.I)
    if regular_match:
        regular_display = clean_html(regular_match.group(1))
        after_regular = value_html[regular_match.end() :]
        price_display = clean_html(after_regular) or price_display

    eur_value = int(sort_match.group(1)) / 100.0
    title_match = re.search(r'title="([^"]+)"', cells[2])
    sale_note = html.unescape(title_match.group(1)).strip() if title_match else clean_html(cells[2])
    sale_note = " ".join(sale_note.split())

    url_match = re.search(r'data-url="([^"]+)"', row_html)
    buy_url = urljoin(ESHOP_PRICES_BASE_URL, html.unescape(url_match.group(1))) if url_match else None

    return RegionPrice(
        code=code,
        country=country,
        price_display=price_display,
        regular_display=regular_display,
        eur_value=eur_value,
        vnd_value=eur_value * eur_to_vnd,
        sale_note=sale_note,
        buy_url=buy_url,
        available=True,
    )


def scrape_eshop_prices(game: GameConfig) -> ScrapeReport:
    page_html = fetch_text(game.eshop_prices_url)
    fx_updated, eur_to_vnd = get_fx()
    game_name = extract_game_name(page_html, game.name)
    available_codes = extract_available_codes(page_html)

    rows_by_code: dict[str, RegionPrice] = {}
    for row_html in re.findall(r'<tr class="pointer".*?</tr>', page_html, re.S | re.I):
        row = parse_price_row(row_html, eur_to_vnd)
        if row:
            rows_by_code[row.code] = row

    all_codes = available_codes or sorted(rows_by_code)
    results: list[RegionPrice] = []
    for code in all_codes:
        row = rows_by_code.get(code)
        if row:
            results.append(row)
        else:
            results.append(
                RegionPrice(
                    code=code,
                    country=REGION_NAMES.get(code, code),
                    price_display=None,
                    regular_display=None,
                    eur_value=None,
                    vnd_value=None,
                    sale_note="",
                    buy_url=None,
                    available=False,
                    note="No price row found for this game/region in the HTML.",
                )
            )

    return ScrapeReport(
        game_key=game.key,
        game_name=game_name,
        source="eshop-prices-html",
        source_url=game.eshop_prices_url,
        generated_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        fx_updated_utc=fx_updated,
        eur_to_vnd=eur_to_vnd,
        results=results,
    )


def format_summary(report: ScrapeReport, top: int = 10, include_missing: bool = True) -> str:
    lowest = report.lowest
    lines = [
        f"Game: {report.game_name}",
        f"Source: {report.source}",
        f"Checked: {report.generated_at_utc}",
        f"FX: 1 EUR = {vnd(report.eur_to_vnd)} ({report.fx_updated_utc})",
        f"Regions with price: {len(report.comparable)}/{len(report.results)}",
    ]

    if lowest:
        lines.extend(
            [
                "",
                "Lowest:",
                (
                    f"{lowest.country} ({lowest.code}) - {lowest.price_display} "
                    f"~ {vnd(lowest.vnd_value)}"
                ),
            ]
        )
        if lowest.sale_note:
            lines.append(lowest.sale_note)

    lines.extend(["", f"Top {min(top, len(report.comparable))}:"])
    lines.append("| # | Region | Price | VND | Sale |")
    lines.append("|---:|---|---:|---:|---|")
    for index, row in enumerate(report.comparable[:top], start=1):
        sale = row.sale_note or "-"
        price = row.price_display or "-"
        lines.append(
            f"| {index} | {row.country} ({row.code}) | {price} | {vnd(row.vnd_value)} | {sale} |"
        )

    if include_missing and report.missing:
        lines.extend(["", "No price row in HTML:"])
        lines.append(", ".join(f"{row.country} ({row.code})" for row in report.missing))

    return "\n".join(lines)


def format_full_table(report: ScrapeReport) -> str:
    lines = [
        "| Region | Price | Regular | EUR sort | Approx VND | Sale / note |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in report.comparable:
        note = row.sale_note or row.note or "-"
        lines.append(
            "| "
            f"{row.country} ({row.code}) | "
            f"{row.price_display or '-'} | "
            f"{row.regular_display or '-'} | "
            f"{eur(row.eur_value)} | "
            f"{vnd(row.vnd_value)} | "
            f"{note} |"
        )
    for row in report.missing:
        lines.append(
            "| "
            f"{row.country} ({row.code}) | - | - | - | - | "
            f"{row.note or 'not available'} |"
        )
    return "\n".join(lines)


def run(game_arg: str, games_path: str | None = None) -> ScrapeReport:
    games = load_games(games_file_path(games_path))
    game = resolve_game(game_arg, games)
    return scrape_eshop_prices(game)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape Nintendo eShop regional prices from public HTML."
    )
    parser.add_argument(
        "game",
        nargs="?",
        default=os.environ.get("DEFAULT_GAME", "blasphemous-2"),
        help="Game key from games.json, or an eshop-prices.com game URL.",
    )
    parser.add_argument("--games-file", help="Path to games.json.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of tables.")
    parser.add_argument("--top", type=int, default=10, help="Number of cheapest regions to show.")
    parser.add_argument(
        "--all-regions",
        action="store_true",
        help="Deprecated compatibility flag. The HTML scraper always checks all listed regions.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Deprecated compatibility flag. The HTML scraper fetches one comparison page.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Print every region after the summary.",
    )
    args = parser.parse_args(argv)

    try:
        report = run(args.game, args.games_file)
    except (HTTPError, URLError, TimeoutError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_summary(report, top=args.top))
        if args.full:
            print()
            print(format_full_table(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
