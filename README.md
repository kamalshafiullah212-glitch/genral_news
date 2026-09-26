# Pashto News Bot — Daily Schedule (اقتصاد، نېټه، اسعار، ټکنالوژي، دندې، ټولنیز)

A single Telegram bot that publishes in Pashto from verified public sources and
runs on GitHub Actions, every 30 minutes.

## Daily schedule (Asia/Kabul, UTC+4:30)

```
06:00  📅 date  +  💱 rates   (once a day, right after six o'clock)
...    🗞 economy / afghan_tech / jobs / global_tech / social
       published as soon as a fresh item is found, all day long
```

* **📅 date and 💱 rates are time-locked to 06:00.** The first run at or after
  06:00 Kabul publishes both, once each; no later run repeats them that day.
* **All other news is published as soon as it appears.** Every run looks for
  the freshest unseen item, scanning the five news categories in a fair
  rotation (`economy -> afghan_tech -> jobs -> global_tech -> social`) so no
  category starves.
* **At least 15 posts per day** (and at most about 20, `DAILY_MAX_POSTS`). The
  day is paced from 06:00 so posts are spread out instead of dumped in one
  burst. If the bot falls behind the 15/day pace it automatically allows older
  items (up to `CATCHUP_MAX_AGE_HOURS`) to catch up; if there is simply no
  suitable item, that run posts nothing rather than inventing news.
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

> **economy:** Forex Factory's public feed only covers the current week. If the
> week has no `USD` + `High` impact event (or the feed is briefly rate-limited),
> that category is skipped for the run and the cycle moves on — nothing is
> invented to fill the slot.

Every post includes the category, a short Pashto text, `📅 نېټه`, `🔗 سرچینه`,
the original link, and category hashtags. Numbers, currencies and dates are
copied from the source and are never invented.

## Grok writing (news categories)

For `economy`, `afghan_tech`, `jobs`, `global_tech` and `social`, the bot asks
Grok to turn the supplied title/summary into a short natural Pashto post. The
API call uses only the supplied facts and numbers. **If `GROK_API_KEY` is absent
or the call fails, a safe built-in summary is posted instead** so the daily
15-post target is not lost. `date` and `rates` are never rewritten — their
numbers come straight from the source.

`GROK_MODEL` can be set as a repository **variable** (Settings → Secrets and
variables → Actions → Variables). Default when empty: `grok-4.1-fast`.

## Setup

1. Create/use the GitHub repository that contains this folder.
2. **Settings → Secrets and variables → Actions → New repository secret**:
   * `TELEGRAM_BOT_TOKEN` — from @BotFather
   * `TELEGRAM_CHAT_ID` — target chat/channel id (digits only, from @userinfobot)
   * `GROK_API_KEY` — optional, for AI-written Pashto wording
3. Open the **Actions** tab, select *General News Bot — Daily Pashto News*,
   and click **Run workflow** once to verify.

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
| `DAY_ANCHOR_HOUR` | Kabul hour when date + rates are published (default `6`) |
| `DAILY_MIN_POSTS` | Minimum posts per news day (default `15`) |
| `DAILY_MAX_POSTS` | Upper bound / pacing target per news day (default `20`) |
| `CATCHUP_MAX_AGE_HOURS` | Age limit used while catching up (default `168`) |
| `MAX_POSTS_PER_RUN` | Kept for compatibility; the schedule itself now paces posting |
| `MAX_AGE_HOURS` | Ignore older news items in normal mode (workflow uses 36) |
| `MIN_SCORE` | Minimum relevance score for RSS candidates |
| `STATE_FILE` | Persisted rotation/duplicate state path |
| `GROK_API_KEY` | Optional xAI key used only for Pashto wording |
| `GROK_MODEL` | Optional model name; defaults to `grok-4.1-fast` |

> GitHub cron is UTC, so 06:00 Kabul is `01:30 UTC` (`cron: "*/30 * * * *"`
> already covers it). GitHub may delay a scheduled run by a few minutes; the bot
> posts at the first run at or after 06:00, so a short delay is harmless.

## Duplicate protection

State stores the URL/hash of every posted item, the news rotation index, the
current news-day key, how many posts that day already had, and whether the daily
date/rates were published. Old entries are pruned after 45 days. The workflow
restores the newest `newsbot-state-*` cache and saves the updated state after
every run with `if: always()`, so the schedule survives failures.
