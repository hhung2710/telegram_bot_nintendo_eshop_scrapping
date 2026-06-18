#!/usr/bin/env python3
"""
Telegram bot that triggers Nintendo eShop HTML scraping.

This uses Telegram long polling through the HTTP Bot API, so it can run from
this computer without a public webhook URL.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from html import escape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from eshop_scraper import (
    format_full_table,
    format_summary,
    games_file_path,
    load_games,
    run,
    vnd,
)


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


load_dotenv()

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
DEFAULT_GAME = os.environ.get("DEFAULT_GAME", "blasphemous-2").strip()
ALLOWED_CHAT_IDS = {
    item.strip()
    for item in os.environ.get("ALLOWED_CHAT_IDS", "").split(",")
    if item.strip()
}
API_TIMEOUT = 35
MESSAGE_LIMIT = 3900


def api_url(method: str) -> str:
    if not TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
    return f"https://api.telegram.org/bot{TOKEN}/{method}"


def call_api(method: str, payload: dict[str, Any] | None = None, timeout: int = API_TIMEOUT) -> Any:
    data = None
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if payload:
        data = urlencode(payload).encode("utf-8")
    request = Request(api_url(method), data=data, headers=headers, method="POST")
    with urlopen(request, timeout=timeout) as response:
        body = json.load(response)
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {body}")
    return body.get("result")


def send_message(chat_id: int | str, text: str, parse_mode: str | None = None) -> None:
    for chunk in split_message(text):
        payload = {
            "chat_id": str(chat_id),
            "text": chunk,
            "disable_web_page_preview": "true",
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        call_api(
            "sendMessage",
            payload,
        )


def split_message(text: str) -> list[str]:
    if len(text) <= MESSAGE_LIMIT:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        extra = len(line) + 1
        if current and current_len + extra > MESSAGE_LIMIT:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += extra
    if current:
        chunks.append("\n".join(current))
    return chunks


def is_allowed(chat_id: int | str) -> bool:
    return not ALLOWED_CHAT_IDS or str(chat_id) in ALLOWED_CHAT_IDS


def help_text() -> str:
    return (
        "Nintendo eShop price bot\n\n"
        "Commands:\n"
        "/price [game name|shortcut|URL] - scrape all listed regions and show the cheapest prices\n"
        "/full [game] - scrape and return every region row\n"
        "/games - list saved shortcuts\n"
        "/status - check that the bot is alive\n"
        "/help - show this message\n\n"
        f"Default game: {DEFAULT_GAME}\n"
        "Saved shortcuts live in games.json, but you can also type a game name or paste an eshop-prices.com game URL."
    )


def list_games() -> str:
    games = load_games(games_file_path())
    lines = ["Saved shortcuts:"]
    for key, game in sorted(games.items()):
        lines.append(f"- {key}: {game.name}")
    lines.append("")
    lines.append("You can still use /price with any game name or eShop-Prices game URL.")
    return "\n".join(lines)


def game_arg(parts: list[str]) -> str:
    return parts[1].strip() if len(parts) > 1 and parts[1].strip() else DEFAULT_GAME


def code(text: object) -> str:
    return f"<code>{escape(str(text))}</code>"


def pre(text: str) -> str:
    return f"<pre>{escape(text)}</pre>"


def telegram_summary(report, top: int = 10, include_missing: bool = True) -> str:
    lowest = report.lowest
    lines = [
        f"<b>{escape(report.game_name)}</b>",
        f"<b>Checked:</b> {escape(report.generated_at_utc)}",
        f"<b>FX:</b> 1 EUR = {escape(vnd(report.eur_to_vnd))}",
        f"<b>Regions:</b> {len(report.comparable)}/{len(report.results)} with price",
    ]

    if lowest:
        lines.extend(
            [
                "",
                "<b>LOWEST PRICE</b>",
                (
                    f"<b>{escape(lowest.country)} ({escape(lowest.code)})</b> - "
                    f"{code(lowest.price_display or '-')} -> "
                    f"<b>{escape(vnd(lowest.vnd_value))}</b>"
                ),
            ]
        )
        if lowest.sale_note:
            lines.append(escape(lowest.sale_note))

    rows = report.comparable[:top]
    if rows:
        table = aligned_table(
            ["#", "Region", "Price", "VND"],
            [
                [
                    str(index),
                    f"{row.country} ({row.code})",
                    row.price_display or "-",
                    vnd(row.vnd_value),
                ]
                for index, row in enumerate(rows, start=1)
            ],
        )
        lines.extend(["", f"<b>Top {len(rows)}</b>", pre(table)])

    if include_missing and report.missing:
        missing = ", ".join(f"{row.country} ({row.code})" for row in report.missing)
        lines.extend(["", "<b>No price row in HTML</b>", escape(missing)])

    return "\n".join(lines)


def telegram_full_messages(report) -> list[str]:
    messages = [telegram_summary(report, top=10, include_missing=True)]
    rows = [
        [
            f"{row.country} ({row.code})",
            row.price_display or "-",
            row.regular_display or "-",
            vnd(row.vnd_value),
            row.sale_note or row.note or "-",
        ]
        for row in report.comparable + report.missing
    ]

    header = ["Region", "Price", "Regular", "VND", "Note"]
    for index, chunk in enumerate(chunk_rows(header, rows), start=1):
        messages.append(f"<b>All Regions {index}</b>\n{pre(aligned_table(header, chunk))}")
    return messages


def chunk_rows(header: list[str], rows: list[list[str]], limit: int = 3000) -> list[list[list[str]]]:
    chunks: list[list[list[str]]] = []
    current: list[list[str]] = []
    for row in rows:
        trial = current + [row]
        if current and len(aligned_table(header, trial)) > limit:
            chunks.append(current)
            current = [row]
        else:
            current = trial
    if current:
        chunks.append(current)
    return chunks


def aligned_table(header: list[str], rows: list[list[str]]) -> str:
    widths = [
        min(28, max(len(str(item[index])) for item in [header] + rows))
        for index in range(len(header))
    ]

    def cell(value: str, index: int) -> str:
        text = str(value)
        width = widths[index]
        if len(text) > width:
            text = text[: max(1, width - 1)] + "…"
        return text.ljust(width)

    output = ["  ".join(cell(value, index) for index, value in enumerate(header))]
    output.append("  ".join("-" * width for width in widths))
    for row in rows:
        output.append("  ".join(cell(value, index) for index, value in enumerate(row)))
    return "\n".join(output)


def handle_command(chat_id: int, text: str) -> None:
    if not is_allowed(chat_id):
        send_message(chat_id, "This chat is not allowed to use this bot.")
        return

    parts = text.split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower()

    if command in {"/start", "/help"}:
        send_message(chat_id, help_text())
        return

    if command == "/status":
        send_message(chat_id, "Bot is running.")
        return

    if command == "/games":
        send_message(chat_id, list_games())
        return

    if command in {"/price", "/lowest", "/full"}:
        selected = game_arg(parts)
        send_message(chat_id, f"Scraping {selected} across all configured regions. This can take a moment.")
        report = run(selected)
        include_full = command == "/full"
        if include_full:
            for message in telegram_full_messages(report):
                send_message(chat_id, message, parse_mode="HTML")
            return

        send_message(chat_id, telegram_summary(report, top=10, include_missing=True), parse_mode="HTML")
        return

    send_message(chat_id, "Unknown command. Send /help.")


def poll_loop() -> None:
    if not TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN before running the bot.")

    call_api("deleteWebhook", {"drop_pending_updates": "false"})
    me = call_api("getMe")
    print(f"Bot connected as @{me.get('username', 'unknown')}")

    offset = 0
    while True:
        try:
            updates = call_api(
                "getUpdates",
                {
                    "timeout": "25",
                    "offset": str(offset),
                    "allowed_updates": json.dumps(["message"]),
                },
                timeout=40,
            )
            for update in updates:
                offset = max(offset, int(update["update_id"]) + 1)
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                chat_id = chat.get("id")
                text = (message.get("text") or "").strip()
                if chat_id is not None and text.startswith("/"):
                    try:
                        handle_command(int(chat_id), text)
                    except Exception as exc:  # Keep the bot alive after command failures.
                        print(traceback.format_exc())
                        send_message(chat_id, f"Command failed: {exc}")
        except HTTPError as exc:
            if exc.code == 409:
                print("Telegram reports another getUpdates poller is running for this token.")
                time.sleep(10)
            else:
                print(f"Telegram HTTP error: {exc}")
                time.sleep(5)
        except (URLError, TimeoutError) as exc:
            print(f"Network error: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    poll_loop()
