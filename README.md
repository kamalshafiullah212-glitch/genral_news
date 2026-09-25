# Pashto News Bot — Technology & Economy (ټکنالوژۍ او اقتصاد)

A single Telegram bot that collects, filters, de-duplicates and posts
**technology + economy/forex news in Pashto**, running automatically on
GitHub Actions.

**AI news is strictly excluded** — ChatGPT, Gemini, Claude, OpenAI, LLM,
"AI tools" etc. are filtered out and never posted by this bot
(a separate AI News Bot handles that topic).

## How it works

```
RSS/News Sources → Fetch → AI blocklist → Quality filter → Deduplicate/story-merge
      → Category detection → Priority scoring → Pashto rendering → Telegram
```

* **33 sources**: tech (The Verge, TechCrunch, Ars, Android Authority, …)
  plus economy/forex (Investing.com, CNBC, MarketWatch, FXStreet, ForexLive,
  CoinDesk, Cointelegraph, Yahoo Finance) plus Google News queries for
  Afghanistan tech/economy and forex.
* **Pashto output** is template-based and never invents facts: the original
  title is kept verbatim inside the post, and numbers/percentages are copied
  exactly (`$4.4 billion`, `5.085%`, `25 basis points`).
* **Deduplication** via story merging — the same event from several outlets
  becomes one post listing all sources.
* **Rate limited** Telegram sender (respects Telegram limits).

## Setup

1. Create a repository on GitHub (empty is fine).
2. Push these files (see commands below).
3. **Settings → Secrets and variables → Actions → New repository secret**, add:
   * `TELEGRAM_BOT_TOKEN` — token from @BotFather
   * `TELEGRAM_CHAT_ID` — your chat/group id (get it from @userinfobot)
4. Open the **Actions** tab and run *General News Bot — Technology & Economy*
   once (button: **Run workflow**) to verify.

The schedule is defined in `.github/workflows/news.yml`:
`- cron: "*/30 * * * *"` (every 30 minutes). GitHub may delay scheduled runs
by a few minutes.

## Local dry run

```powershell
pip install -r requirements.txt
$env:DRY_RUN="1"
python bot.py
```

`DRY_RUN=1` prints the posts instead of sending them to Telegram.

## Configuration

Environment variables (GitHub Secrets in CI):

| Variable | Meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token (required to send) |
| `TELEGRAM_CHAT_ID` | Target chat id (required to send) |
| `DRY_RUN` | `1` = print only, never send |
| `MAX_POSTS_PER_RUN` | Posts per run (workflow uses 4) |
| `MAX_AGE_HOURS` | Ignore older news (workflow uses 36) |
