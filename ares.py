import json
import logging
import random
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    import cloudscraper
    from bs4 import BeautifulSoup
except ImportError:
    raise SystemExit("O'rnating: pip install cloudscraper beautifulsoup4")

BASE_URL = "https://horo.mail.ru/prediction"
OUT_DIR = Path("horo_data")
LOG_DIR = Path("logs")
STATE_FILE = Path(".horo_state.json")

OUT_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 30
SLEEP_MIN, SLEEP_MAX = 5, 10

ZODIACS = {
    "aries": "Овен",
    "taurus": "Телец",
    "gemini": "Близнецы",
    "cancer": "Рак",
    "leo": "Лев",
    "virgo": "Дева",
    "libra": "Весы",
    "scorpio": "Скорпион",
    "sagittarius": "Стрелец",
    "capricorn": "Козерог",
    "aquarius": "Водолей",
    "pisces": "Рыбы",
}

DAYS = ["yesterday", "today", "tomorrow"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "scraper.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("ares")


def now_tashkent_iso() -> str:
    return datetime.now(timezone(timedelta(hours=5))).isoformat(timespec="seconds")


def relative_target_date(day_name: str) -> str:
    today = date.today()
    offsets = {"yesterday": -1, "today": 0, "tomorrow": 1}
    return (today + timedelta(days=offsets[day_name])).isoformat()


def deep_parse(html: str):
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    paragraphs = soup.find_all("p")
    candidate_paragraphs = []

    for p in paragraphs:
        text = p.get_text(" ", strip=True)
        if len(text) <= 80:
            continue
        lower = text.lower()
        if "бесплатный гороскоп" in lower or "советы астрологов" in lower:
            continue
        candidate_paragraphs.append(text)

    if candidate_paragraphs:
        return "\n\n".join(candidate_paragraphs)

    blocks = []
    for div in soup.find_all("div"):
        text = div.get_text(" ", strip=True)
        if len(text) > 200:
            blocks.append(text)

    if blocks:
        blocks.sort(key=len, reverse=True)
        return blocks[0]

    return None


def fetch_with_retry(scraper, url, headers):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = scraper.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                return response

            if response.status_code in (403, 429):
                log.warning(
                    "HTTP %s -> %s (urinish %s/%s), %ss kutilmoqda",
                    response.status_code,
                    url,
                    attempt,
                    MAX_RETRIES,
                    RETRY_BACKOFF_SEC,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SEC)
                    continue
                return response

            log.error("HTTP %s -> %s", response.status_code, url)
            return response

        except Exception as exc:
            log.error("So'rov xatosi (%s/%s) %s: %s", attempt, MAX_RETRIES, url, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC)
            else:
                return None

    return None


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except Exception as exc:
        log.warning("%s o'qishda xato: %s", path, exc)
        return default


def save_json(path: Path, data):
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temp_path.replace(path)


def load_state():
    return load_json(STATE_FILE, {"initialized": False, "last_success_date": None})


def is_bootstrap_ready():
    state = load_state()
    if not state.get("initialized"):
        return False

    today_str = date.today().isoformat()
    last_success = state.get("last_success_date")
    if not last_success:
        return False

    # Agar repository uzoq vaqt ishlamagan bo'lsa, xavfsiz yo'l bilan 3 kunni qayta quramiz.
    try:
        gap_days = (date.fromisoformat(today_str) - date.fromisoformat(last_success)).days
    except ValueError:
        return False
    return gap_days == 0 or gap_days == 1


def valid_day_file(day_name: str, expected_date: str) -> bool:
    path = OUT_DIR / f"{day_name}.json"
    data = load_json(path, None)
    if not isinstance(data, dict):
        return False
    if data.get("date_for") != expected_date:
        return False
    zodiacs = data.get("zodiacs", {})
    return isinstance(zodiacs, dict) and len(zodiacs) >= len(ZODIACS)


def rotate_relative_days():
    """Oldingi natijalarni relative nomlar bo'yicha bir kun oldinga siljitadi."""
    yesterday_path = OUT_DIR / "yesterday.json"
    today_path = OUT_DIR / "today.json"
    tomorrow_path = OUT_DIR / "tomorrow.json"

    old_yesterday_archive = OUT_DIR / "_old_yesterday.json"
    if old_yesterday_archive.exists():
        old_yesterday_archive.unlink()

    if yesterday_path.exists():
        yesterday_path.replace(old_yesterday_archive)

    if today_path.exists():
        today_path.replace(yesterday_path)
        old_data = load_json(yesterday_path, {})
        if isinstance(old_data, dict):
            old_data["day"] = "yesterday"
            save_json(yesterday_path, old_data)

    if tomorrow_path.exists():
        tomorrow_path.replace(today_path)
        old_data = load_json(today_path, {})
        if isinstance(old_data, dict):
            old_data["day"] = "today"
            save_json(today_path, old_data)

    if old_yesterday_archive.exists():
        old_yesterday_archive.unlink()


def scrape_day(scraper, day_name: str, expected_date: str) -> bool:
    day_file = OUT_DIR / f"{day_name}.json"
    existing = load_json(
        day_file,
        {
            "schema_version": 2,
            "date_generated": None,
            "date_for": expected_date,
            "day": day_name,
            "source": "mail.ru",
            "zodiacs": {},
        },
    )

    existing["schema_version"] = 2
    existing["day"] = day_name
    existing["date_for"] = expected_date
    existing["date_generated"] = now_tashkent_iso()
    existing["source"] = "mail.ru"
    existing.setdefault("zodiacs", {})

    success = 0
    for slug, name_ru in ZODIACS.items():
        url = f"{BASE_URL}/{slug}/{day_name}/"
        headers = {
            "Referer": "https://www.google.com/",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
        }

        response = fetch_with_retry(scraper, url, headers)
        if response is not None and response.status_code == 200:
            text = deep_parse(response.text)
            if text:
                existing["zodiacs"][slug] = {
                    "name_ru": name_ru,
                    "text": text,
                    "fetched_at": now_tashkent_iso(),
                    "char_count": len(text),
                }
                success += 1
                log.info("OK %s (%s) - %s ta belgi", name_ru, slug, len(text))
            else:
                log.warning("EMPTY %s (%s) - matn topilmadi", name_ru, slug)
        else:
            status = response.status_code if response is not None else "NO_RESPONSE"
            log.error("FAIL %s (%s) - %s", name_ru, slug, status)

        save_json(day_file, existing)
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    log.info("%s tugadi: %s/%s", day_name.upper(), success, len(ZODIACS))
    return success == len(ZODIACS)


def main():
    state = load_state()
    bootstrap = not is_bootstrap_ready()

    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )

    log.info("=" * 60)
    log.info("MAIL.RU SCRAPER ishga tushdi")

    if bootstrap:
        log.info("Birinchi/ tiklash rejimi: YESTERDAY + TODAY + TOMORROW")
        dates = {day: relative_target_date(day) for day in DAYS}
        results = [scrape_day(scraper, day, dates[day]) for day in DAYS]
        ok = all(results)
    else:
        today_str = date.today().isoformat()
        last_success = state.get("last_success_date")
        if last_success != today_str:
            rotate_relative_days()

        target_date = relative_target_date("tomorrow")
        log.info("Kunlik rejim: faqat TOMORROW -> %s", target_date)
        ok = scrape_day(scraper, "tomorrow", target_date)

    if ok:
        state = load_state()
        state["initialized"] = True
        state["last_success_date"] = date.today().isoformat()
        state["last_success_at"] = now_tashkent_iso()
        state.setdefault("translation_initialized", False)
        save_json(STATE_FILE, state)
        log.info("Scraper muvaffaqiyatli yakunlandi")
    else:
        log.error("Scraper to'liq muvaffaqiyatli bo'lmadi; state yangilanmadi")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
