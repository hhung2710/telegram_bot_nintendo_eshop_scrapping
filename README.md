# Nintendo eShop Telegram Price Bot

This project scrapes public HTML for Nintendo eShop regional prices and runs a
local Telegram bot that triggers the scrape on command.

## Telegram setup

1. Open Telegram and message `@BotFather`.
2. Send `/newbot`, choose a name and username, then copy the token.
3. In this folder, export the token:

```sh
export TELEGRAM_BOT_TOKEN="123456789:your_token"
```

You can also create a local `.env` file using `.env.example` as the template.

4. Start the bot from this computer:

```sh
python3 telegram_bot.py
```

The bot uses long polling, so you do not need a domain, webhook, or public port.

## Bot commands

```text
/price blasphemous-2
/price Blasphemous 2
/price https://eshop-prices.com/games/11624-blasphemous-2
/full blasphemous-2
/games
/status
```

`/price` returns the lowest region and top 10 cheapest regions. `/full` also
returns every region row, split across Telegram messages if needed.

`games.json` is only for saved shortcuts and the default game. If a game is not
listed there, the bot searches eShop-Prices HTML by name and uses the first
matching game page.

## CLI usage

```sh
python3 nintendo-eshop-price-check.py blasphemous-2 --full
python3 nintendo-eshop-price-check.py blasphemous-2 --json
```

## Add a shortcut

You do not need to add every game to `games.json`. Add an entry only when you
want a short alias or a stable default:

```json
{
  "some-game": {
    "name": "Some Game",
    "eshop_prices_url": "https://eshop-prices.com/games/12345-some-game"
  }
}
```

The scraper reads the page HTML, extracts every country listed by that page,
then converts the normalized EUR comparison value to VND using the current FX
feed.
