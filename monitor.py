from __future__ import annotations

import argparse
import html
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, Tag


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
STATE_PATH = ROOT / "data" / "state.json"
LOG_PATH = ROOT / "logs" / "monitor.log"
BASE_URL = "https://www.kotanamur.be"

# These URLs already contain the filters supplied by the user.
SEARCHES = (
    (
        "Kots",
        "https://www.kotanamur.be/kots?moveIn%5Bperiod%5D=asap&"
        "rentalDurations%5B%5D=ten_to_twelve_months&type%5B%5D=11&"
        "minRent=300&maxRent=420&sort=l.rentWoCharges&direction=asc",
    ),
    (
        "Studios",
        "https://www.kotanamur.be/studios?moveIn%5Bperiod%5D=asap&"
        "rentalDurations%5B%5D=ten_to_twelve_months&type%5B%5D=14&"
        "minRent=300&maxRent=420&sort=l.lastPublishedOrPremiumAt&direction=desc",
    ),
)


@dataclass(frozen=True)
class Listing:
    listing_id: str
    category: str
    title: str
    rent: str
    neighborhood: str
    availability: str
    activity: str
    description: str
    url: str


def load_dotenv(path: Path = ENV_PATH) -> None:
    """Load a small, dependency-free subset of .env syntax."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a whole number") from exc


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def setup_logging(verbose: bool = False) -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("rental_monitor")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0 Safari/537.36 PersonalRentalMonitor/1.0"
            ),
            "Accept-Language": "fr-BE,fr;q=0.9,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def fetch_html(session: requests.Session, url: str, timeout: int) -> str:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            if "text/html" not in response.headers.get("Content-Type", ""):
                raise RuntimeError(f"Unexpected response type from {url}")
            return response.text
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
    raise RuntimeError(f"Could not fetch {url}: {last_error}") from last_error


def clean_text(node: Tag | None) -> str:
    return " ".join(node.stripped_strings) if node else ""


def parse_listing(article: Tag, category: str) -> Listing | None:
    detail_link = article.select_one('a.link-to-detail[href^="/KN/"]')
    if not detail_link:
        return None

    href = str(detail_link.get("href", "")).strip()
    match = re.fullmatch(r"/KN/(\d+)", href)
    if not match:
        return None

    image = article.select_one("img[alt]")
    title = str(image.get("alt", "")).strip() if image else ""
    if not title:
        title = clean_text(article.select_one(".listing-teaser-type")) or "Rental listing"

    activity = ""
    activity_list = article.select_one(".listing-teaser-activity-data")
    if activity_list:
        for item in activity_list.select("li"):
            classes = set(item.get("class", []))
            if "listing-tag-available" not in classes:
                activity = clean_text(item)
                if activity:
                    break

    description = clean_text(
        article.select_one(".listing-teaser-description-large .item-description")
        or article.select_one(".item-description")
    )

    return Listing(
        listing_id=f"KN/{match.group(1)}",
        category=category,
        title=title,
        rent=clean_text(article.select_one(".listing-rent--rent-wo-charges")),
        neighborhood=clean_text(article.select_one(".listing-teaser-neighborhood")),
        availability=clean_text(article.select_one(".listing-tag-available")),
        activity=activity,
        description=description,
        url=urljoin(BASE_URL, href),
    )


def pagination_links(soup: BeautifulSoup, first_url: str) -> Iterable[str]:
    parsed_first = urlsplit(first_url)
    root_path = parsed_first.path.rstrip("/")
    page_pattern = re.compile(rf"{re.escape(root_path)}/(\d+)/?")

    for anchor in soup.select("a[href]"):
        candidate = urljoin(first_url, str(anchor.get("href", "")))
        parsed = urlsplit(candidate)
        if parsed.netloc != parsed_first.netloc:
            continue
        page_match = page_pattern.fullmatch(parsed.path)
        if page_match:
            # The site emits equivalent filter queries in several encodings
            # (for example [] and [0]). Canonicalizing to the original query
            # prevents crawling the same result page more than once.
            yield parsed_first._replace(
                path=f"{root_path}/{page_match.group(1)}", fragment=""
            ).geturl()


def crawl_search(
    session: requests.Session,
    category: str,
    first_url: str,
    timeout: int,
    delay: float,
    max_pages: int,
    logger: logging.Logger,
) -> dict[str, Listing]:
    queue = [first_url]
    visited: set[str] = set()
    listings: dict[str, Listing] = {}

    while queue and len(visited) < max_pages:
        page_url = queue.pop(0)
        if page_url in visited:
            continue
        if visited and delay > 0:
            time.sleep(delay)

        page_html = fetch_html(session, page_url, timeout)
        soup = BeautifulSoup(page_html, "html.parser")
        visited.add(page_url)

        for article in soup.select("article.listing-teaser"):
            listing = parse_listing(article, category)
            if listing:
                listings[listing.listing_id] = listing

        for link in pagination_links(soup, first_url):
            if link not in visited and link not in queue:
                queue.append(link)

    if queue:
        logger.warning(
            "%s has more than %d result pages; increase MAX_PAGES_PER_SEARCH",
            category,
            max_pages,
        )
    logger.info("%s: found %d listings across %d page(s)", category, len(listings), len(visited))
    return listings


def collect_listings(logger: logging.Logger) -> dict[str, Listing]:
    timeout = env_int("REQUEST_TIMEOUT_SECONDS", 25)
    delay = env_float("REQUEST_DELAY_SECONDS", 0.75)
    max_pages = env_int("MAX_PAGES_PER_SEARCH", 10)
    if max_pages < 1:
        raise ValueError("MAX_PAGES_PER_SEARCH must be at least 1")

    session = make_session()
    combined: dict[str, Listing] = {}
    for category, url in SEARCHES:
        combined.update(
            crawl_search(session, category, url, timeout, delay, max_pages, logger)
        )
    return combined


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"version": 1, "seen_ids": [], "last_successful_check": None}
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Cannot read {STATE_PATH}. Fix or move that file before continuing."
        ) from exc
    if not isinstance(state.get("seen_ids"), list):
        raise RuntimeError(f"Invalid state file: {STATE_PATH}")
    return state


def save_state(seen_ids: set[str], listing_count: int) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "seen_ids": sorted(seen_ids),
        "last_successful_check": datetime.now(timezone.utc).isoformat(),
        "last_listing_count": listing_count,
    }
    temporary = STATE_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, STATE_PATH)


def telegram_credentials() -> tuple[str, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    placeholders = {"", "paste_your_bot_token_here", "paste_your_chat_id_here"}
    if token in placeholders or chat_id in placeholders:
        raise RuntimeError(
            "Telegram is not configured. Copy .env.example to .env, then add "
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
        )
    return token, chat_id


def telegram_request(token: str, method: str, **kwargs) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        response = requests.request(method="POST", url=url, timeout=25, **kwargs)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(f"Telegram request failed: {exc}") from exc
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram rejected the request: {payload.get('description', payload)}")
    return payload


def send_telegram(text: str) -> None:
    token, chat_id = telegram_credentials()
    telegram_request(
        token,
        "sendMessage",
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "false",
        },
    )


def listing_message(listing: Listing) -> str:
    lines = [
        f"🏠 <b>New {html.escape(listing.category.lower())} listing</b>",
        f"<b>{html.escape(listing.title)}</b>",
    ]
    if listing.rent:
        lines.append(f"💶 {html.escape(listing.rent)} excluding charges")
    if listing.neighborhood:
        lines.append(f"📍 {html.escape(listing.neighborhood)}")
    if listing.availability:
        lines.append(f"📅 {html.escape(listing.availability)}")
    if listing.activity:
        lines.append(f"🕒 {html.escape(listing.activity)}")
    if listing.description:
        excerpt = listing.description
        if len(excerpt) > 550:
            excerpt = excerpt[:547].rstrip() + "..."
        lines.extend(("", html.escape(excerpt)))
    lines.extend(("", f'<a href="{html.escape(listing.url, quote=True)}">Open listing</a>'))
    return "\n".join(lines)


def run_check(logger: logging.Logger, dry_run: bool = False) -> int:
    listings = collect_listings(logger)
    if not listings:
        raise RuntimeError(
            "No listings were parsed. The website may have changed; state was not modified."
        )

    if dry_run:
        print(f"Successfully parsed {len(listings)} matching listings.")
        for listing in list(listings.values())[:5]:
            print(f"- {listing.listing_id}: {listing.title} | {listing.rent} | {listing.url}")
        return 0

    state_exists = STATE_PATH.exists()
    state = load_state()
    seen = set(str(item) for item in state["seen_ids"])
    current_ids = set(listings)

    if not state_exists:
        send_telegram(
            "✅ <b>Kotanamur monitor started</b>\n"
            f"Tracking {len(current_ids)} existing matching listings. "
            "Only newly discovered listings will be sent from now on."
        )
        save_state(current_ids, len(listings))
        logger.info("Initialized baseline with %d listings", len(listings))
        return 0

    new_ids = sorted(current_ids - seen)
    if not new_ids:
        logger.info("No new matching listings")
        return 0

    delivered: set[str] = set()
    for listing_id in new_ids:
        listing = listings[listing_id]
        try:
            send_telegram(listing_message(listing))
            delivered.add(listing_id)
            logger.info("Sent Telegram alert for %s", listing.url)
        except Exception:
            logger.exception("Could not notify for %s; it will be retried", listing.url)

    save_state(seen | delivered, len(listings))
    if len(delivered) != len(new_ids):
        raise RuntimeError(
            f"Delivered {len(delivered)} of {len(new_ids)} alerts; failures will retry"
        )
    return 0


def find_chat_ids() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or token == "paste_your_bot_token_here":
        raise RuntimeError("Add TELEGRAM_BOT_TOKEN to .env first")
    payload = telegram_request(token, "getUpdates", data={"limit": 100})
    chats: dict[str, str] = {}
    for update in payload.get("result", []):
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat") or {}
        if "id" not in chat:
            continue
        display = " ".join(
            str(chat.get(key, "")).strip()
            for key in ("first_name", "last_name", "title", "username")
            if str(chat.get(key, "")).strip()
        )
        chats[str(chat["id"])] = display or str(chat.get("type", "chat"))
    if not chats:
        print("No chat found. Open your bot in Telegram, press Start or send /start, then retry.")
        return 1
    print("Chat IDs found:")
    for chat_id, name in chats.items():
        print(f"- {chat_id} ({name})")
    print("Copy your ID into TELEGRAM_CHAT_ID in .env.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor filtered Kotanamur listings")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--watch", action="store_true", help="check forever at a fixed interval")
    mode.add_argument("--dry-run", action="store_true", help="test scraping without Telegram or state")
    mode.add_argument("--test-telegram", action="store_true", help="send a Telegram test message")
    mode.add_argument("--find-chat-id", action="store_true", help="show chat IDs from recent bot messages")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    logger = setup_logging(args.verbose)

    if args.find_chat_id:
        return find_chat_ids()
    if args.test_telegram:
        send_telegram("✅ Kotanamur monitor: Telegram notifications are working.")
        print("Test notification sent.")
        return 0
    if args.dry_run:
        return run_check(logger, dry_run=True)
    if not args.watch:
        return run_check(logger)

    interval = env_int("CHECK_INTERVAL_SECONDS", 900)
    if interval < 300:
        raise ValueError("CHECK_INTERVAL_SECONDS must be at least 300 (5 minutes)")
    logger.info("Monitor running; checking every %d seconds", interval)
    while True:
        started = time.monotonic()
        try:
            run_check(logger)
        except KeyboardInterrupt:
            logger.info("Monitor stopped")
            return 0
        except Exception:
            logger.exception("Check failed; the next check will retry")
        elapsed = time.monotonic() - started
        try:
            time.sleep(max(1, interval - elapsed))
        except KeyboardInterrupt:
            logger.info("Monitor stopped")
            return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
