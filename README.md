# Pashto News Bot — Technology & Economy (ټکنالوژۍ او اقتصاد)

A single Telegram bot that publishes one post per run in a fixed seven-category
cycle, in Pashto, from verified public sources. It runs on GitHub Actions.

## Sequential publication cycle

```
💵 economy -> 📅 date -> 💱 rates -> 🇦🇫 afghan_tech
-> 💼 jobs -> 🌍 global_tech -> 📱 social -> (new cycle)
```

* **One post per run** (`MAX_POSTS_PER_RUN=1`). The workflow runs every 30
  minutes, so categories are spaced out instead of flooding the channel.
* The current position is stored in `state/posted.json` and restored/saved with
  the GitHub Actions cache. A category that has no suitable item is skipped for
  that run; the next suitable category is posted and the cycle continues.
* The **date** and **rates** categories post at most once per day.
* Duplicate links and titles are remembered and never posted twice.

## Category sources

| Category | Source |
|---|---|
| 💵 economy | **Forex Factory** public weekly calendar JSON — only `USD` + `High` impact events |
| 📅 date | Asia/Kabul local date + Gregorian + Solar Hijri (Jalali) |
| 💱 rates | ExchangeRate-API open USD endpoint (`USD, EUR, GBP, PKR, IRR` against AFN) with provider update time |
| 🇦🇫 afghan_tech | Google News RSS — Afghanistan technology/internet/telecom/startup |
| 💼 jobs | ReliefWeb Afghanistan jobs RSS (confirmed feed) + Google News jobs fallback |
| 🌍 global_tech | The Verge, TechCrunch, Ars Technica, NVIDIA, Google News global technology (AI included) |
| 📱 social | Google News RSS — Facebook/Meta, Instagram, TikTok, YouTube, Telegram, WhatsApp, Snapchat, LinkedIn, X updates |

Every post includes the category, a short Pashto text, `📅 نېټه`, `🔗 سرچینه`,
the original link, and category hashtags. Numbers, currencies and dates are
copied from the source and are never invented.

## Optional Grok writing

If `GROK_API_KEY` is set as a GitHub Secret, the bot asks Grok to rewrite the
supplied title/summary into a short natural Pashto post. The API call uses only
the supplied facts and numbers. If the key is absent or the API fails, the bot
uses a safe built-in fallback and still posts.

`GROK_MODEL` can be set as a repository **variable** (Settings → Secrets and
variables → Actions → Variables). Default when empty: `grok-4.1-fast`.

## Setup

1. Create/use the GitHub repository that contains this folder.
2. **Settings → Secrets and variables → Actions → New repository secret**:
   * `TELEGRAM_BOT_TOKEN` — from @BotFather
   * `TELEGRAM_CHAT_ID` — target chat/channel id (digits only, from @userinfobot)
   * `GROK_API_KEY` — optional, for AI-written Pashto wording
3. Open the **Actions** tab and run *General News Bot — Technology & Economy*
   once with **Run workflow** to verify.

## Local dry run

```powershell
pip install -r requirements.txt
$env:DRY_RUN="1"
$env:STATE_FILE="_dry_state.json"
python bot.py
```

`DRY_RUN=1` prints the selected post instead of sending it and does not write
the real state file.

## Configuration

Environment variables:

| Variable | Meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token (required to send) |
| `TELEGRAM_CHAT_ID` | Target chat id (required to send) |
| `DRY_RUN` | `1` = print only, never send |
| `MAX_POSTS_PER_RUN` | Keep at `1` for the sequential cycle |
| `MAX_AGE_HOURS` | Ignore older news items (workflow uses 36) |
| `MIN_SCORE` | Minimum relevance score for RSS candidates |
| `STATE_FILE` | Persisted rotation/duplicate state path |
| `GROK_API_KEY` | Optional xAI key used only for Pashto wording |
| `GROK_MODEL` | Optional model name; defaults to `grok-4.1-fast` |

## Duplicate protection

State stores the URL/hash of every posted item and the current rotation index.
Old entries are pruned after 45 days. The workflow restores the newest
`newsbot-state-*` cache and saves the updated state after every run with
`if: always()`, so the cycle survives failures.
