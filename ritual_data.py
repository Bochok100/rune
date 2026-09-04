import json
import os
from datetime import datetime, timedelta

DB_FILE = "users_db.json"
PAGES = "https://Bochok100.github.io/rune"

BASE_MAP = {"1": "А", "2": "Ц", "3": "У", "4": "Г"}
AMINO_ACIDS = {
    "Аргинин": {"codons": ["ЦГЦ", "ЦГУ", "ЦГА", "ЦГГ", "АГА", "АГГ"], "runes": ["Ч", "Y"]},
    "Аланин": {"codons": ["ГЦУ", "ГЦГ", "ГЦЦ", "ГЦА"], "runes": [")", "¥", "𐰉", "𐰈"]},
    "Аспарагин": {"codons": ["ААУ", "ААЦ"], "runes": ["ʎ"]},
    "Аспарагиновая к-та": {"codons": ["ГАУ", "ГАЦ"], "runes": ["*", "1"]},
    "Валин": {"codons": ["ГУУ", "ГУЦ", "ГУА", "ГУГ"], "runes": ["𐰓", "9", "ς"]},
    "Глютамин": {"codons": ["ЦАА", "ЦАГ"], "runes": ["Λ", "П"]},
    "Глютаминовая к-та": {"codons": ["ГАА", "ГАГ"], "runes": ["Y"]},
    "Гистидин": {"codons": ["ЦАУ", "ЦАЦ"], "runes": ["𐰓"]},
    "Глицин": {"codons": ["ГГУ", "ГГА", "ГГЦ", "ГГГ"], "runes": ["☺"]},
    "Стоп-кодон": {"codons": ["УАА"], "runes": ["33"]},
    "Изолейцин": {"codons": ["АУУ", "АУЦ", "АУА"], "runes": ["I|", "Є"]},
    "Лейцин": {"codons": ["УУА", "УУГ", "ЦУУ", "ЦУЦ", "ЦУА", "ЦУГ"], "runes": ["Y", "J"]},
    "Лизин": {"codons": ["ААА", "ААГ"], "runes": ["↑"]},
    "Пирролизин": {"codons": ["УАГ"], "runes": ["ᛟ"]},
    "Метионин": {"codons": ["АУГ"], "runes": ["Г"]},
    "Пролин": {"codons": ["ЦЦУ", "ЦЦГ", "ЦЦЦ", "ЦЦА"], "runes": ["ᛉ"]},
    "Серин": {"codons": ["УЦУ", "УЦГ", "УЦЦ", "УЦА", "АГУ", "АГЦ"], "runes": ["D", "☺"]},
    "Триптофан": {"codons": ["УГГ"], "runes": ["⌂"]},
    "Тирозин": {"codons": ["УАУ", "УАЦ"], "runes": ["ᛒ", "ᛃ"]},
    "Треонин": {"codons": ["АЦУ", "АЦГ", "АЦЦ", "АЦА"], "runes": ["ㅋ", "N", "◁", "F"]},
    "Фенилаланин": {"codons": ["УУУ", "УУЦ"], "runes": ["X", "|"]},
    "Цистеин": {"codons": ["УГУ", "УГЦ"], "runes": ["︽", "h"]},
    "Селеноцистеин": {"codons": ["УГА"], "runes": ["M"]},
}

RUNE_IMAGES = {
    "Аргинин": ["Аргинин2.jpg", "Аргинин.jpg"],
    "Аланин": ["Аланин4.jpg", "Аланин3.jpg", "Аланин2.jpg", "Аланин.jpg"],
    "Аспарагин": ["Аспарагин.jpg"],
    "Аспарагиновая к-та": ["Аспарагиновая к-та.jpg", "Аспарагиновая к-та2.jpg"],
    "Валин": ["Валин2.jpg", "Валин3.jpg", "Валин.jpg"],
    "Глютамин": ["Глютамин.jpg", "Глютамин2.jpg"],
    "Глютаминовая к-та": ["Глютаминовая к-та.jpg"],
    "Гистидин": ["Гистидин.jpg"],
    "Глицин": ["Глицин.jpg"],
    "Стоп-кодон": ["Стоповой кодон.jpg"],
    "Изолейцин": ["Изолейцин2.jpg", "Изолейцин.jpg"],
    "Лейцин": ["Лейцин.jpg", "Лейцин2.jpg"],
    "Лизин": ["Лизин.jpg"],
    "Пирролизин": ["Пирролизин.jpg"],
    "Метионин": ["Метионин.jpg"],
    "Пролин": ["Пролин.jpg"],
    "Серин": ["Серин.jpg", "Серин2.jpg"],
    "Триптофан": ["Триптофан.jpg"],
    "Тирозин": ["Тирозин.jpg", "Тирозин2.jpg"],
    "Треонин": ["Треонин.jpg", "Треонин2.jpg", "Треонин3.jpg", "Треонин4.jpg"],
    "Фенилаланин": ["Фенилаланин.jpg", "Фенилаланин 2.jpg"],
    "Цистеин": ["Цистеин.jpg", "Цистеин.jpg"],
    "Селеноцистеин": ["Селеноцистеин.jpg"],
}


def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return json.load(f)
    return {}


def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f)


def empty_user_record(now: datetime) -> dict:
    return {
        "trial_end": now.isoformat(),
        "next_ritual_time": now.isoformat(),
        "notified": 0,
        "paid": False,
        "referrer": None,
        "referrals_count": 0,
        "ritual_step": 0,
        "last_active": now.isoformat(),
        "notified_incomplete": False,
        "notified_12h": False,
        "notified_inactive": False,
        "special_day_notified": "",
        "unlimited_rituals": False,
    }


def ensure_user_record(db: dict, user_id: str, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    data = db.get(user_id)
    if not isinstance(data, dict):
        data = empty_user_record(now)
        db[user_id] = data
    return data


def days_left_from(trial_end: datetime, now: datetime) -> int:
    time_left = trial_end - now
    if time_left.total_seconds() <= 0:
        return 0
    return int(time_left.total_seconds() / 86400) + (1 if time_left.total_seconds() % 86400 > 0 else 0)


def restore_user_access(db: dict, user_id: str, days: int, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    data = ensure_user_record(db, user_id, now)
    current_end = datetime.fromisoformat(data.get("trial_end", now.isoformat()))
    start_date = current_end if current_end > now else now
    data["trial_end"] = (start_date + timedelta(days=days)).isoformat()
    data["notified"] = 0
    data["paid"] = True
    return data


def ritual_unlimited(data: dict) -> bool:
    return bool(data.get("unlimited_rituals"))


def schedule_next_ritual(data: dict, now: datetime | None = None, hours: int = 12) -> None:
    now = now or datetime.now()
    if ritual_unlimited(data):
        data["next_ritual_time"] = now.isoformat()
    else:
        data["next_ritual_time"] = (now + timedelta(hours=hours)).isoformat()


def set_ritual_mode(db: dict, user_id: str, mode: str, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    data = ensure_user_record(db, user_id, now)
    if mode == "unlimited":
        data["unlimited_rituals"] = True
        data["next_ritual_time"] = now.isoformat()
        data["ritual_step"] = 0
    elif mode == "off":
        data["unlimited_rituals"] = False
    else:
        data["next_ritual_time"] = now.isoformat()
        data["ritual_step"] = 0
        data["notified_12h"] = False
        data["notified_incomplete"] = False
    return data


def parse_ritual_admin_args(args: list[str], self_id: str) -> tuple[str, str] | None:
    on = {"inf", "unlimited", "on", "forever"}
    off = {"off", "stop"}
    parts = [a.strip() for a in args if a.strip()]
    if not parts:
        return self_id, "once"
    if len(parts) == 1:
        token = parts[0].lower()
        if token in on:
            return self_id, "unlimited"
        if token in off:
            return self_id, "off"
        if parts[0].isdigit():
            return parts[0], "once"
        return None
    if len(parts) == 2 and parts[0].isdigit():
        token = parts[1].lower()
        if token in on:
            return parts[0], "unlimited"
        if token in off:
            return parts[0], "off"
        return None
    return None


def format_access_status(user_id: str, data: dict, now: datetime | None = None) -> str:
    now = now or datetime.now()
    trial_end = datetime.fromisoformat(data.get("trial_end", now.isoformat()))
    next_ritual = datetime.fromisoformat(data.get("next_ritual_time", now.isoformat()))
    left = days_left_from(trial_end, now)
    active = now < trial_end
    ritual_ready = ritual_unlimited(data) or now >= next_ritual
    unlimited = ritual_unlimited(data)
    return (
        f"👤 ID: `{user_id}`\n"
        f"{'✅ Доступ активен' if active else '🔒 Доступ неактивен'}\n"
        f"📅 До: `{trial_end.strftime('%Y-%m-%d %H:%M')}`\n"
        f"⏳ Осталось дней: **{left}**\n"
        f"🔮 Обряд: {'без лимита' if unlimited else ('можно провести' if ritual_ready else 'ожидание таймера')}\n"
        f"💳 paid: `{data.get('paid', False)}`"
    )


def get_greeting_text(user_data, now):
    trial_end = datetime.fromisoformat(user_data.get("trial_end", now.isoformat()))
    greeting = (
        "Приветствую. Это Ваш цифровой помощник в достижении гармонии. "
        "Используем мудрость салгын кут и силу рунических символов, "
        "чтобы помочь вам восполнить утраченный ресурс.\n\n"
    )
    if now < trial_end:
        greeting += f"🎁 **У вас активно {days_left_from(trial_end, now)} дня доступа!**\n\n"
    else:
        greeting += (
            "⚠️ **Ваша подписка неактивна.**\n"
            "Пройдите обряд, чтобы выбрать тариф и получить доступ к результатам.\n\n"
        )
    return greeting


def find_rune_image(amino: str, index: int) -> str | None:
    files = RUNE_IMAGES.get(amino, [])
    if not files:
        return None
    if index >= len(files) or files[index] is None:
        img_name = files[0]
    else:
        img_name = files[index]
    path = os.path.join("images", "runes", img_name)
    return path if os.path.exists(path) else None


def rune_page_url(amino: str, index: int) -> str | None:
    files = RUNE_IMAGES.get(amino, [])
    if not files:
        return None
    img_name = files[index] if index < len(files) and files[index] else files[0]
    from urllib.parse import quote
    return f"{PAGES}/images/runes/{quote(img_name)}"
