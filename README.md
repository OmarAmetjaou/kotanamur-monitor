# Kotanamur Telegram monitor

This free local monitor checks the two filtered Kotanamur searches supplied in the project, remembers listings it has already seen, and sends new matching listings to Telegram. It scans every results page, so the price-sorted kot search does not hide a new listing on a later page.

The monitor only runs while this Windows computer is switched on and connected to the internet.

## Free 24/7 cloud option

The included `.github/workflows/monitor.yml` runs the same monitor on GitHub Actions every 15 minutes, even when your computer is off. Use a public repository for free standard-runner usage. The `.env` file is ignored and must never be uploaded.

After uploading the project to GitHub, open **Settings → Secrets and variables → Actions** and create these two repository secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Then open **Actions → Check Kotanamur listings → Run workflow**. After that first successful run, GitHub checks automatically. The workflow commits only the non-secret listing state and a monthly heartbeat. Once the cloud run is confirmed, remove the local Windows task with `powershell -ExecutionPolicy Bypass -File .\uninstall_startup_task.ps1` to avoid duplicate alerts.

## 1. Prepare Python

Open PowerShell in this folder and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

## 2. Create the Telegram bot

1. In Telegram, open the verified **@BotFather** account.
2. Send `/newbot` and follow its instructions.
3. Copy the bot token into `TELEGRAM_BOT_TOKEN` in `.env`.
4. Open your new bot, press **Start**, or send `/start`.
5. Find your chat ID:

```powershell
.\.venv\Scripts\python.exe .\monitor.py --find-chat-id
```

6. Copy the displayed number into `TELEGRAM_CHAT_ID` in `.env`.

Do not share `.env`; it contains the secret that controls your bot.

## 3. Test everything

Test the website parser without Telegram:

```powershell
.\.venv\Scripts\python.exe .\monitor.py --dry-run
```

Send one Telegram test message:

```powershell
.\.venv\Scripts\python.exe .\monitor.py --test-telegram
```

Initialize the monitor:

```powershell
.\.venv\Scripts\python.exe .\monitor.py
```

The first real run records all existing listings and sends one startup message. It does **not** flood Telegram with old listings. Later runs send a message for each newly discovered listing.

## 4. Run automatically

Install and immediately start a Windows scheduled task:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_startup_task.ps1
```

The task stays running in the background and checks every 15 minutes. It restarts at Windows sign-in. To remove it:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_startup_task.ps1
```

To run it in the foreground instead, use `powershell -ExecutionPolicy Bypass -File .\run_monitor.ps1` and leave the window open.

## Files and troubleshooting

- Settings and Telegram credentials: `.env`
- Deduplication state: `data/state.json`
- Runtime log: `logs/monitor.log`
- Search URLs: the `SEARCHES` constant near the top of `monitor.py`

The default interval is deliberately polite to the website. Keep it at 5 minutes or longer. If the site's HTML structure changes, the monitor stops before modifying its state and writes the error to the log.
