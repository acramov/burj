import html
import json
import logging
import os
import random
import re
import time
from datetime import date, timedelta, timezone, datetime
from pathlib import Path

try:
    import requests
except ImportError:
    raise SystemExit("O'rnating: pip install requests")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

OUT_DIR = Path("horo_data")
LOG_DIR = Path("logs")
STATE_FILE = Path(".horo_state.json")
LOG_DIR.mkdir(exist_ok=True)

DAYS = ["yesterday", "today", "tomorrow"]
ZODIAC_UZ = {
    "aries": "Qo'y",
    "taurus": "Buzoq",
    "gemini": "Egizaklar",
    "cancer": "Qisqichbaqa",
    "leo": "Arslon",
    "virgo": "Sunbula",
    "libra": "Tarozi",
    "scorpio": "Chayon",
    "sagittarius": "O'qotar",
    "capricorn": "Jadiy",
    "aquarius": "Qovg'a",
    "pisces": "Baliq",
}

MAX_RETRIES = 3
DEFAULT_RETRY_WAIT_SEC = 10
MAX_RETRY_WAIT_SEC = 120
REQUEST_TIMEOUT = 180
SLEEP_BETWEEN_CALLS = (1, 2)
BATCH_SIZE = 20
BATCH_PAUSE_SEC = (10, 15)

CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
REFUSAL_MARKERS = [
    "i cannot",
    "i can't",
    "i'm sorry",
    "as an ai",
    "i am unable",
    "cannot assist",
    "i'm unable",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "translate.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("translate")


def tashkent_now_iso():
    return datetime.now(timezone(timedelta(hours=5))).isoformat(timespec="seconds")


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


def normalize_paragraphs(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    paragraphs = []
    current = []
    for line in lines:
        if line:
            current.append(line)
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs).strip()


def paragraph_count(text: str) -> int:
    normalized = normalize_paragraphs(text)
    return 0 if not normalized else len(normalized.split("\n\n"))


def translated_html(text: str) -> str:
    paragraphs = [p.strip() for p in normalize_paragraphs(text).split("\n\n") if p.strip()]
    return "\n".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)


def validate_translation(original: str, translated: str):
    if not translated or not translated.strip():
        return False, "bo'sh javob"

    lower = translated.lower()
    for marker in REFUSAL_MARKERS:
        if marker in lower:
            return False, f"model xato/rad javobini qaytardi: {marker}"

    if translated.strip() == original.strip():
        return False, "tarjima original bilan bir xil"

    letters = [ch for ch in translated if ch.isalpha()]
    cyr_count = len(CYRILLIC_RE.findall(translated))
    if letters and (cyr_count / len(letters)) > 0.10:
        return False, f"kirill alifbosi ulushi yuqori: {cyr_count}"

    ratio = len(translated) / max(len(original), 1)
    if ratio < 0.40:
        return False, f"juda qisqa: {ratio:.2f}"
    if ratio > 2.50:
        return False, f"juda uzun: {ratio:.2f}"

    original_paragraphs = paragraph_count(original)
    translated_paragraphs = paragraph_count(translated)
    if original_paragraphs > 1 and translated_paragraphs != original_paragraphs:
        return False, f"abzats soni mos emas: original={original_paragraphs}, tarjima={translated_paragraphs}"

    return True, None


def build_prompt(text: str) -> str:
    # Abzatslarni aniq saqlash uchun har bir abzatsdan keyin bo'sh qator talab qilinadi.
    return (
        "Quyidagi rus tilidagi goroskop matnini PROFESSIONAL DARAJADA, tabiiy va ravon O'ZBEK TILIGA tarjima qil.\n\n"
        "QAT'IY QOIDALAR:\n"
        "1. FAQAT o'zbek lotin alifbosida yoz. Kirill harflarini ishlatma.\n"
        "2. Ruscha kalka qilma; ona tilida so'zlashuvchi o'zbek odam yozgandek tabiiy yoz.\n"
        "3. Mazmun, kayfiyat va barcha faktik tafsilotlarni to'liq saqla. Hech narsa qo'shma yoki olib tashlama.\n"
        "4. Matndagi ABZATSLAR SONINI VA TARTIBINI O'ZGARTIRMA. Har bir abzats orasida aynan bitta bo'sh qator qoldir (\\n\\n).\n"
        "5. Raqamlar, vaqtlar, sabablar va munosabatlarni aniq saqla.\n"
        "6. Burj nomlari quyidagicha bo'ladi: "
        + ", ".join(f"{k}={v}" for k, v in ZODIAC_UZ.items())
        + ".\n"
        "7. Faqat tayyor tarjimani qaytar. Hech qanday izoh, sarlavha yoki 'tarjima:' yozma.\n\n"
        "TEKSHIR: kirill yo'qmi, abzatslar saqlanganmi, o'zbekcha tabiiy va grammatik jihatdan to'g'rimi.\n\n"
        f"MATN:\n{text}"
    )


def parse_retry_after(resp):
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            return float(retry_after)
        except (TypeError, ValueError):
            pass

    try:
        body = resp.json()
        msg = str(body.get("error", {}).get("message", ""))
        for pattern in [
            r"try again in\s*([\d.]+)\s*s",
            r"retry after\s*([\d.]+)\s*seconds?",
            r"after\s*([\d.]+)\s*s",
        ]:
            match = re.search(pattern, msg, re.IGNORECASE)
            if match:
                return float(match.group(1))
    except Exception:
        pass
    return None


def call_deepseek(text: str):
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "deepseek-v4-flash",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Siz professional ruscha-o'zbekcha tarjimonsiz. "
                    "Matnni tabiiy adabiy o'zbek tiliga tarjima qilasiz va paragraf tuzilishini saqlaysiz."
                ),
            },
            {"role": "user", "content": build_prompt(text)},
        ],
        "temperature": 0.2,
        "thinking": {"type": "disabled"},
        "max_tokens": max(512, min(8192, len(text) * 3)),
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                DEEPSEEK_API_URL,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )

            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                content = normalize_paragraphs(content)
                valid, reason = validate_translation(text, content)
                if valid:
                    return content
                log.warning("Yaroqsiz tarjima: %s (%s/%s)", reason, attempt, MAX_RETRIES)
                if attempt < MAX_RETRIES:
                    time.sleep(2)
                    continue
                return None

            if resp.status_code == 429:
                retry_after = parse_retry_after(resp)
                wait_sec = min(
                    retry_after if retry_after is not None else DEFAULT_RETRY_WAIT_SEC * (2 ** (attempt - 1)),
                    MAX_RETRY_WAIT_SEC,
                )
                wait_sec += random.uniform(0, 2)
                log.warning("429: %.1fs kutish (%s/%s)", wait_sec, attempt, MAX_RETRIES)
                if attempt < MAX_RETRIES:
                    time.sleep(wait_sec)
                    continue
                return None

            if resp.status_code in (500, 502, 503, 504):
                wait_sec = min(
                    DEFAULT_RETRY_WAIT_SEC * (2 ** (attempt - 1)),
                    MAX_RETRY_WAIT_SEC,
                )
                log.warning("Server xatosi %s: %ss kutish", resp.status_code, wait_sec)
                if attempt < MAX_RETRIES:
                    time.sleep(wait_sec)
                    continue
                return None

            log.error("DeepSeek HTTP %s: %s", resp.status_code, resp.text[:500])
            return None

        except requests.exceptions.Timeout:
            wait_sec = min(
                DEFAULT_RETRY_WAIT_SEC * (2 ** (attempt - 1)),
                MAX_RETRY_WAIT_SEC,
            )
            log.warning("Timeout: %ss kutish", wait_sec)
            if attempt < MAX_RETRIES:
                time.sleep(wait_sec)
                continue
            return None
        except Exception as exc:
            log.error("Kutilmagan DeepSeek xatosi: %s", exc)
            return None

    return None


def translate_day(day: str):
    path = OUT_DIR / f"{day}.json"
    data = load_json(path, None)
    if not isinstance(data, dict):
        log.error("%s topilmadi", path)
        return False

    todo = []
    cached = 0
    for slug, info in data.get("zodiacs", {}).items():
        if info.get("translated") and info.get("translated_paragraphs"):
            cached += 1
        elif info.get("text"):
            todo.append((slug, info))

    new_translated = 0
    for idx, (slug, info) in enumerate(todo, start=1):
        log.info("%s (%s) tarjima qilinmoqda", info.get("name_ru", slug), slug)
        result = call_deepseek(info["text"])
        if result:
            result = normalize_paragraphs(result)
            info["translated"] = result
            info["translated_paragraphs"] = [p for p in result.split("\n\n") if p.strip()]
            info["translated_html"] = translated_html(result)
            info["translated_at"] = tashkent_now_iso()
            info["translated_via"] = "deepseek-v4-flash"
            info["translated_char_count"] = len(result)
            new_translated += 1
            log.info("OK %s", slug)
        else:
            log.error("FAIL %s", slug)

        save_json(path, data)

        if idx < len(todo):
            if idx % BATCH_SIZE == 0:
                time.sleep(random.uniform(*BATCH_PAUSE_SEC))
            else:
                time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))

    total = len(data.get("zodiacs", {}))
    complete = sum(1 for x in data.get("zodiacs", {}).values() if x.get("translated"))
    log.info("%s: yangi=%s, kesh=%s, jami=%s/%s", day.upper(), new_translated, cached, complete, total)
    return total > 0 and complete == total


def is_bootstrap():
    state = load_json(STATE_FILE, {"initialized": False, "translation_initialized": False})
    return not state.get("translation_initialized", False)


def mark_translation_initialized():
    state = load_json(STATE_FILE, {})
    state["translation_initialized"] = True
    state["translation_initialized_at"] = tashkent_now_iso()
    save_json(STATE_FILE, state)


def main():
    if not DEEPSEEK_API_KEY:
        raise SystemExit("DEEPSEEK_API_KEY topilmadi. GitHub Actions Secret qo'shing.")

    bootstrap = is_bootstrap()
    targets = DAYS if bootstrap else ["tomorrow"]

    log.info("=" * 60)
    log.info("TRANSLATE ishga tushdi: %s", "3 kun" if bootstrap else "faqat tomorrow")

    all_ok = True
    for index, day in enumerate(targets):
        ok = translate_day(day)
        all_ok = all_ok and ok
        if index < len(targets) - 1 and ok:
            time.sleep(random.uniform(5, 10))

    if not all_ok:
        raise SystemExit(1)

    if bootstrap:
        mark_translation_initialized()


if __name__ == "__main__":
    main()
