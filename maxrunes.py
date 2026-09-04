#!/usr/bin/env python3
"""Бот рун для мессенджера MAX. Telegram не трогает. Токен: MAX_BOT_TOKEN в .env"""
import asyncio
import json
import logging
import os
import urllib.parse
from datetime import datetime, timedelta

from dotenv import load_dotenv

from ritual_data import (
    AMINO_ACIDS,
    BASE_MAP,
    PAGES,
    RUNE_IMAGES,
    days_left_from,
    ensure_user_record,
    get_greeting_text,
    load_db,
    restore_user_access,
    rune_page_url,
    save_db,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

API = "https://platform-api2.max.ru"
FSM_FILE = "max_fsm.json"
MARKER_FILE = "max_marker.txt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip().replace('"', "").replace("'", "") if value else default


def db_key(user_id: int) -> str:
    return f"max:{user_id}"


def load_fsm() -> dict:
    if os.path.exists(FSM_FILE):
        with open(FSM_FILE, "r") as f:
            return json.load(f)
    return {}


def save_fsm(data: dict):
    with open(FSM_FILE, "w") as f:
        json.dump(data, f)


def fsm_get(user_id: int) -> dict:
    data = load_fsm()
    return data.get(str(user_id), {})


def fsm_set(user_id: int, payload: dict):
    data = load_fsm()
    data[str(user_id)] = payload
    save_fsm(data)


def fsm_clear(user_id: int):
    data = load_fsm()
    data.pop(str(user_id), None)
    save_fsm(data)


def kb(*rows):
    return [{"type": "inline_keyboard", "payload": {"buttons": list(rows)}}]


def btn(text: str, payload: str) -> dict:
    return {"type": "callback", "text": text, "payload": payload}


def link(text: str, url: str) -> dict:
    return {"type": "link", "text": text, "url": url}


def throw_row(color_emoji: str) -> list:
    return [btn(f"{color_emoji} {i}", f"throw_{i}") for i in range(1, 5)]


def menu_kb():
    return kb(
        [link("📖 Об авторе", f"{PAGES}/author.html")],
        [link("📜 История метода", f"{PAGES}/method.html")],
        [link("🌬️ Буор, Ийэ и Салгын Кут", f"{PAGES}/kut.html")],
        [btn("🔮 Начать обряд", "start_ritual")],
        [btn("🆔 Мой ID", "myid")],
        [link("🕯 Подготовка", f"{PAGES}/prep.html"), link("💬 Отзывы", f"{PAGES}/reviews.html")],
    )


class MaxApi:
    def __init__(self, session, token: str):
        self.session = session
        self.headers = {"Authorization": token, "Content-Type": "application/json"}

    async def get(self, path: str, params=None):
        async with self.session.get(f"{API}{path}", headers=self.headers, params=params) as resp:
            text = await resp.text()
            if resp.status >= 400:
                logging.error("MAX GET %s -> %s %s", path, resp.status, text[:500])
                resp.raise_for_status()
            return json.loads(text) if text else {}

    async def post(self, path: str, params=None, json_body=None):
        async with self.session.post(
            f"{API}{path}", headers=self.headers, params=params, json=json_body
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                logging.error("MAX POST %s -> %s %s", path, resp.status, text[:500])
                resp.raise_for_status()
            return json.loads(text) if text else {}

    async def patch(self, path: str, json_body=None):
        async with self.session.patch(f"{API}{path}", headers=self.headers, json=json_body) as resp:
            text = await resp.text()
            if resp.status >= 400:
                logging.error("MAX PATCH %s -> %s %s", path, resp.status, text[:500])
                resp.raise_for_status()
            return json.loads(text) if text else {}

    async def send(self, user_id: int, text: str, attachments=None):
        body = {"text": text, "format": "markdown"}
        if attachments:
            body["attachments"] = attachments
        return await self.post("/messages", params={"user_id": user_id}, json_body=body)

    async def answer(self, callback_id: str, notification: str = "Ок"):
        try:
            await self.post("/answers", params={"callback_id": callback_id}, json_body={"notification": notification})
        except Exception as e:
            logging.warning("callback answer: %s", e)


async def ensure_max_user(user_id: int) -> dict:
    db = load_db()
    key = db_key(user_id)
    now = datetime.now()
    if key not in db or not isinstance(db.get(key), dict):
        data = ensure_user_record(db, key, now)
        data["trial_end"] = (now + timedelta(days=3)).isoformat()
        save_db(db)
        return data
    return ensure_user_record(db, key, now)


async def cmd_start(api: MaxApi, user_id: int, payload: str | None = None):
    data = await ensure_max_user(user_id)
    if payload and payload.startswith("ref_"):
        ref = payload.split("_", 1)[1]
        db = load_db()
        if ref != str(user_id) and f"max:{ref}" in db:
            restore_user_access(db, f"max:{ref}", 3)
            save_db(db)
            try:
                await api.send(int(ref), "🎉 По вашей ссылке присоединился новый участник! +3 дня доступа.")
            except Exception:
                pass
    text = get_greeting_text(data, datetime.now())
    text += "Нажмите кнопку ниже или напишите «обряд»."
    await api.send(user_id, text, menu_kb())


async def start_ritual(api: MaxApi, user_id: int):
    db = load_db()
    key = db_key(user_id)
    data = ensure_user_record(db, key)
    now = datetime.now()
    next_ritual = datetime.fromisoformat(data.get("next_ritual_time", now.isoformat()))
    if now < next_ritual:
        left = next_ritual - now
        hours, rem = divmod(int(left.total_seconds()), 3600)
        minutes = rem // 60
        await api.send(
            user_id,
            f"⏳ Обряд уже проведен! Следующий через {hours} ч. {minutes} мин.\n"
            f"Пригласите друга: `https://max.ru/?start=ref_{user_id}`",
        )
        return
    data["ritual_step"] = 1
    data["last_active"] = now.isoformat()
    save_db(db)
    fsm_set(user_id, {"step": "blue", "complex": 1, "runes": [], "aminos": [], "images": []})
    await api.send(
        user_id,
        "🔮 **Комплекс 1.** Брось палочки и посмотри на **синюю** грань. Сколько точек?",
        kb(throw_row("🔵")),
    )


async def on_throw(api: MaxApi, user_id: int, value: str):
    st = fsm_get(user_id)
    step = st.get("step")
    if step == "blue":
        st["blue"] = value
        st["step"] = "green"
        fsm_set(user_id, st)
        await api.send(user_id, "Теперь **зелёная** грань. Сколько точек?", kb(throw_row("🟢")))
        return
    if step == "green":
        st["green"] = value
        st["step"] = "red"
        fsm_set(user_id, st)
        await api.send(user_id, "Теперь **красная** грань. Сколько точек?", kb(throw_row("🔴")))
        return
    if step != "red":
        await api.send(user_id, "Нажмите «Начать обряд».", menu_kb())
        return
    st["red"] = value
    triplet = BASE_MAP[st["red"]] + BASE_MAP[st["green"]] + BASE_MAP[st["blue"]]
    amino, runes = "Неизвестно", []
    for name, a_data in AMINO_ACIDS.items():
        if triplet in a_data["codons"]:
            amino, runes = name, a_data["runes"]
            break
    st["amino"] = amino
    st["runes_opts"] = runes
    st["car"] = 0
    if not runes:
        await api.send(user_id, f"Триплет {triplet} не найден.")
        fsm_clear(user_id)
        return
    if len(runes) == 1:
        await pick_rune(api, user_id, st, 0)
        return
    st["step"] = "car"
    fsm_set(user_id, st)
    await show_car(api, user_id, st)


async def show_car(api: MaxApi, user_id: int, st: dict):
    runes = st.get("runes_opts") or []
    amino = st.get("amino", "")
    i = int(st.get("car") or 0)
    total = len(runes)
    symbol = runes[i]
    url = rune_page_url(amino, i)
    rows = []
    if total > 1:
        rows.append([
            btn("◀", f"car_{(i - 1) % total}"),
            btn(f"{i + 1}/{total}", "noop"),
            btn("▶", f"car_{(i + 1) % total}"),
        ])
    rows.append([btn("✅ Выбрать эту руну", f"rune_{i}")])
    if url:
        rows.append([link("📷 Открыть картинку", url)])
    await api.send(user_id, f"🧬 **{amino}**\n🔮 Руна: **{symbol}**", kb(*rows))


async def pick_rune(api: MaxApi, user_id: int, st: dict, index: int):
    amino = st.get("amino", "")
    runes_opts = st.get("runes_opts") or [""]
    rune = runes_opts[index] if index < len(runes_opts) else runes_opts[0]
    files = RUNE_IMAGES.get(amino, [])
    img = files[index] if index < len(files) else (files[0] if files else f"{amino}.jpg")
    st.setdefault("runes", []).append(rune)
    st.setdefault("aminos", []).append(amino)
    st.setdefault("images", []).append(img)
    complex_num = int(st.get("complex") or 1)
    db = load_db()
    key = db_key(user_id)
    data = ensure_user_record(db, key)
    now = datetime.now()
    if complex_num < 3:
        data["ritual_step"] = complex_num + 1
        data["last_active"] = now.isoformat()
        save_db(db)
        st["complex"] = complex_num + 1
        st["step"] = "blue"
        st.pop("blue", None)
        st.pop("green", None)
        st.pop("red", None)
        fsm_set(user_id, st)
        await api.send(
            user_id,
            f"✅ Выбрана руна: **{rune}**\n\n🔮 **Комплекс {complex_num + 1}.** Синяя грань:",
            kb(throw_row("🔵")),
        )
        return
    data["next_ritual_time"] = (now + timedelta(hours=12)).isoformat()
    data["ritual_step"] = 0
    save_db(db)
    aminos = st.get("aminos", [])
    images = st.get("images", [])
    runes = st.get("runes", [])
    fsm_clear(user_id)
    q = urllib.parse.urlencode({"aminos": ",".join(aminos), "images": ",".join(images), "v": int(now.timestamp())})
    result_url = f"{PAGES}/result.html?{q}"
    trial_end = datetime.fromisoformat(data.get("trial_end", now.isoformat()))
    triad = " | ".join(runes)
    if now < trial_end:
        await api.send(
            user_id,
            f"🎉 **Обряд завершён!**\nТриада: **{triad}**\nОсталось дней: {days_left_from(trial_end, now)}",
            kb([link("📖 Получить результаты", result_url)]),
        )
    else:
        await api.send(
            user_id,
            f"🎉 **Обряд завершён!**\nТриада: **{triad}**\n\n"
            "Бесплатный период закончился. Оплата в MAX появится позже; "
            "пока доступ можно открыть в Telegram-боте @sakharune_bot или командой администратора.",
            kb(
                [link("📖 Страница результатов", result_url)],
                [link("✈️ Открыть Telegram-бота", "https://t.me/sakharune_bot")],
            ),
        )


async def handle_callback(api: MaxApi, user_id: int, payload: str, callback_id: str):
    await api.answer(callback_id)
    if payload == "start_ritual":
        await start_ritual(api, user_id)
        return
    if payload == "myid":
        await api.send(user_id, f"Ваш MAX ID: `{user_id}`")
        return
    if payload == "noop":
        return
    if payload.startswith("throw_"):
        await on_throw(api, user_id, payload.split("_")[1])
        return
    if payload.startswith("car_"):
        st = fsm_get(user_id)
        st["car"] = int(payload.split("_")[1])
        fsm_set(user_id, st)
        await show_car(api, user_id, st)
        return
    if payload.startswith("rune_"):
        st = fsm_get(user_id)
        await pick_rune(api, user_id, st, int(payload.split("_")[1]))
        return


async def handle_text(api: MaxApi, user_id: int, text: str):
    raw = (text or "").strip()
    low = raw.lower().split("@")[0].strip()
    if low in {"/start", "start", "начать"}:
        await cmd_start(api, user_id)
        return
    if low in {"/myid", "/id", "мой id", "мойid", "id", "🆔 мой id"}:
        await api.send(user_id, f"Ваш MAX ID: `{user_id}`")
        return
    if low.startswith("/grant"):
        admin = env("MAX_ADMIN_ID")
        if not admin or str(user_id) != admin:
            return
        parts = raw.split()
        db = load_db()
        if len(parts) == 2 and parts[1].isdigit():
            restore_user_access(db, db_key(user_id), int(parts[1]))
            save_db(db)
            await api.send(user_id, f"Доступ продлён на {parts[1]} дн.")
        elif len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            restore_user_access(db, db_key(int(parts[1])), int(parts[2]))
            save_db(db)
            await api.send(user_id, f"Пользователю {parts[1]} начислено {parts[2]} дн.")
        else:
            await api.send(user_id, "Формат: `/grant 30` или `/grant <max_id> 30`")
        return
    if "обряд" in low:
        await start_ritual(api, user_id)
        return
    await cmd_start(api, user_id)


def extract_user_id(update: dict) -> int | None:
    if "user" in update and isinstance(update["user"], dict):
        return update["user"].get("user_id")
    cb = update.get("callback") or {}
    if isinstance(cb, dict) and isinstance(cb.get("user"), dict):
        return cb["user"].get("user_id")
    msg = update.get("message") or {}
    sender = msg.get("sender") or {}
    if sender.get("user_id"):
        return sender["user_id"]
    rec = msg.get("recipient") or {}
    return rec.get("user_id") or update.get("chat_id")


async def handle_update(api: MaxApi, update: dict):
    utype = update.get("update_type")
    user_id = extract_user_id(update)
    if not user_id:
        logging.info("skip update without user: %s", utype)
        return
    if utype == "bot_started":
        await cmd_start(api, user_id, update.get("payload"))
        return
    if utype == "message_callback":
        cb = update.get("callback") or {}
        payload = cb.get("payload") or ""
        await handle_callback(api, user_id, payload, cb.get("callback_id") or "")
        return
    if utype == "message_created":
        msg = update.get("message") or {}
        sender = msg.get("sender") or {}
        if sender.get("is_bot"):
            return
        body = msg.get("body") or {}
        text = body.get("text") or msg.get("text") or ""
        await handle_text(api, user_id, text)


async def wait_for_token() -> str:
    while True:
        load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)
        token = env("MAX_BOT_TOKEN")
        if token and "замените" not in token:
            return token
        logging.warning("MAX_BOT_TOKEN пустой — жду .env (раз в 20 сек). Telegram-бот не трогаю.")
        await asyncio.sleep(20)


async def main():
    import aiohttp

    token = await wait_for_token()
    timeout = aiohttp.ClientTimeout(total=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        api = MaxApi(session, token)
        me = await api.get("/me")
        logging.info("MAX бот онлайн: @%s id=%s", me.get("username"), me.get("user_id"))
        try:
            await api.patch("/me/commands", json_body={
                "commands": [
                    {"name": "start", "description": "Запуск и меню"},
                    {"name": "myid", "description": "Показать мой MAX ID"},
                ]
            })
        except Exception:
            logging.exception("Не удалось зарегистрировать команды MAX")
        marker = None
        if os.path.exists(MARKER_FILE):
            raw = open(MARKER_FILE).read().strip()
            marker = int(raw) if raw.isdigit() else None
        while True:
            try:
                params = {"timeout": 30, "limit": 100, "types": "message_created,message_callback,bot_started"}
                if marker is not None:
                    params["marker"] = marker
                data = await api.get("/updates", params=params)
                marker = data.get("marker", marker)
                if marker is not None:
                    with open(MARKER_FILE, "w") as f:
                        f.write(str(marker))
                for upd in data.get("updates") or []:
                    try:
                        await handle_update(api, upd)
                    except Exception:
                        logging.exception("ошибка обработки MAX update")
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("MAX long poll")
                await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
