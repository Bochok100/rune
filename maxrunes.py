#!/usr/bin/env python3
"""Бот рун для мессенджера MAX. Telegram не трогает. Токен: MAX_BOT_TOKEN в .env"""
import asyncio
import base64
import json
import logging
import os
import ssl
import urllib.parse
from contextvars import ContextVar
from datetime import datetime, timedelta

from dotenv import load_dotenv

from gif_media import ensure_loop_gif
from ritual_data import (
    AMINO_ACIDS,
    BASE_MAP,
    PAGES,
    RUNE_IMAGES,
    days_left_from,
    ensure_user_record,
    find_rune_image,
    get_greeting_text,
    load_db,
    parse_ritual_admin_args,
    restore_user_access,
    ritual_unlimited,
    rune_page_url,
    save_db,
    schedule_next_ritual,
    set_ritual_mode,
    encode_result_payload,
    SUB_PLANS,
    apply_subscription,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

API = "https://platform-api2.max.ru"
ENV_PATH = os.path.join(BASE_DIR, ".env")
current_chat_id: ContextVar[int | None] = ContextVar("current_chat_id", default=None)
FSM_FILE = "max_fsm.json"
MARKER_FILE = "max_marker.txt"
MEDIA_CACHE_FILE = "max_media.json"
GIF_START = "gif1_v2.mp4"
GIF_RITUAL = "gif2_v2.mp4"
BOT_META = {"username": "", "user_id": None}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


RU_CA_URLS = [
    "https://gu-st.ru/content/Other/doc/russian_trusted_root_ca.cer",
    "https://gu-st.ru/content/Other/doc/russian_trusted_sub_ca.cer",
]


def der_or_pem_to_pem(data: bytes) -> bytes:
    if b"BEGIN CERTIFICATE" in data:
        return data
    body = base64.encodebytes(data).decode("ascii")
    pem = "-----BEGIN CERTIFICATE-----\n" + body + "-----END CERTIFICATE-----\n"
    return pem.encode("ascii")


def unverified_ssl() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def download_mincifry_certs() -> list[str]:
    import aiohttp

    cert_dir = os.path.join(BASE_DIR, "certs")
    os.makedirs(cert_dir, exist_ok=True)
    paths = []
    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(ssl=unverified_ssl())
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        for url in RU_CA_URLS:
            name = url.rsplit("/", 1)[-1]
            dest = os.path.join(cert_dir, name)
            try:
                async with session.get(url) as resp:
                    raw = await resp.read()
                if resp.status != 200 or len(raw) < 50:
                    logging.warning("не скачался сертификат %s (%s)", url, resp.status)
                    continue
                pem_path = dest if dest.endswith(".pem") else dest + ".pem"
                with open(pem_path, "wb") as f:
                    f.write(der_or_pem_to_pem(raw))
                paths.append(pem_path)
                logging.info("Сохранён сертификат Минцифры: %s", pem_path)
            except Exception:
                logging.exception("ошибка загрузки %s", url)
    return paths


def ssl_with_certs(paths: list[str]) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    for path in paths:
        try:
            ctx.load_verify_locations(cafile=path)
        except Exception as e:
            logging.warning("не загрузил %s: %s", path, e)
    return ctx


async def max_ssl_context() -> ssl.SSLContext:
    if env("MAX_SSL_INSECURE") in {"1", "true", "yes"}:
        logging.warning("MAX SSL проверка отключена (MAX_SSL_INSECURE=1)")
        return unverified_ssl()
    paths = []
    cert_dir = os.path.join(BASE_DIR, "certs")
    if os.path.isdir(cert_dir):
        paths = [
            os.path.join(cert_dir, name)
            for name in os.listdir(cert_dir)
            if name.endswith((".pem", ".crt", ".cer"))
        ]
    if not paths:
        paths = await download_mincifry_certs()
    if paths:
        return ssl_with_certs(paths)
    logging.warning("Сертификаты Минцифры не найдены — SSL без проверки, иначе MAX API недоступен")
    return unverified_ssl()


def env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip().replace('"', "").replace("'", "") if value else default


def read_env_file_value(path: str, *names: str) -> str:
    if not os.path.exists(path):
        return ""
    try:
        raw = open(path, "r", encoding="utf-8-sig").read()
    except OSError:
        return ""
    wanted = {n.lower() for n in names}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        key, _, val = line.partition("=")
        if key.strip().lower() in wanted:
            return val.strip().strip('"').strip("'")
    return ""


def max_token() -> str:
    load_dotenv(ENV_PATH, override=True)
    for name in ("MAX_BOT_TOKEN", "MAX_TOKEN", "MAX_ACCESS_TOKEN"):
        token = env(name)
        if token and "замените" not in token.lower():
            return token
    return read_env_file_value(ENV_PATH, "MAX_BOT_TOKEN", "MAX_TOKEN", "MAX_ACCESS_TOKEN")


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


def bot_username() -> str:
    return (env("MAX_BOT_USERNAME") or BOT_META.get("username") or "").lstrip("@")


def ref_link(user_id: int) -> str:
    name = bot_username()
    if not name:
        return f"https://max.ru/?start=ref_{user_id}"
    return f"https://max.ru/{name}?start=ref_{user_id}"


def app_deeplink(payload: str = "") -> str:
    name = bot_username()
    if not name:
        return miniapp_url("app.html")
    if payload:
        return f"https://max.ru/{name}?startapp={payload}"
    return f"https://max.ru/{name}?startapp"


def miniapp_url(page: str = "app.html") -> str:
    configured = env("MAX_MINIAPP_URL")
    if configured and page in {"app.html", ""}:
        return configured
    return f"{PAGES}/{page.lstrip('/')}"


def open_app(text: str, payload: str = "") -> dict:
    """Открывает мини-приложение внутри MAX. web_app — username бота, не URL страницы."""
    item = {"type": "open_app", "text": text}
    name = bot_username()
    if name:
        item["web_app"] = name
    bot_id = BOT_META.get("user_id")
    if bot_id:
        item["contact_id"] = int(bot_id)
    if payload:
        item["payload"] = payload
    return item


def throw_row(color_emoji: str) -> list:
    return [btn(f"{color_emoji} {i}", f"throw_{i}") for i in range(1, 5)]


def telegram_bot_name() -> str:
    return env("TELEGRAM_BOT_USERNAME") or "sakharune_bot"


def pay_kb():
    return kb(
        [btn(f"💳 {SUB_PLANS[1]['label']} — {SUB_PLANS[1]['rub']} ₽", "pay_1")],
        [btn(f"💳 {SUB_PLANS[3]['label']} — {SUB_PLANS[3]['rub']} ₽", "pay_3")],
        [btn(f"💳 {SUB_PLANS[12]['label']} — {SUB_PLANS[12]['rub']} ₽", "pay_12")],
    )


def menu_kb():
    return kb(
        [open_app("📖 Об авторе", "author")],
        [open_app("📜 История метода", "method")],
        [open_app("🌬️ Буор, Ийэ и Салгын Кут", "kut")],
        [btn("🔮 Начать обряд", "start_ritual")],
        [open_app("🕯 Подготовка", "prep"), open_app("💬 Отзывы", "reviews")],
    )


def load_media_cache() -> dict:
    if os.path.exists(MEDIA_CACHE_FILE):
        try:
            with open(MEDIA_CACHE_FILE, "r") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_media_cache(data: dict):
    with open(MEDIA_CACHE_FILE, "w") as f:
        json.dump(data, f)


def extract_upload_token(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    if data.get("token"):
        return str(data["token"])
    photos = data.get("photos")
    if isinstance(photos, dict):
        for value in photos.values():
            items = value if isinstance(value, list) else [value]
            for item in reversed(items):
                if isinstance(item, dict) and item.get("token"):
                    return str(item["token"])
    payload = data.get("payload")
    if isinstance(payload, dict) and payload.get("token"):
        return str(payload["token"])
    return ""


def is_not_ready(err: Exception) -> bool:
    text = str(err).lower()
    return "attachment.not.ready" in text or "not.processed" in text or "not ready" in text


def has_open_app(attachments: list) -> bool:
    for att in attachments or []:
        if att.get("type") != "inline_keyboard":
            continue
        for row in att.get("payload", {}).get("buttons", []):
            for item in row:
                if item.get("type") == "open_app":
                    return True
    return False


def demote_open_app(attachments: list) -> list:
    converted = []
    for att in attachments:
        if att.get("type") != "inline_keyboard":
            converted.append(att)
            continue
        rows = []
        for row in att.get("payload", {}).get("buttons", []):
            new_row = []
            for item in row:
                if item.get("type") == "open_app":
                    payload = item.get("payload") or ""
                    new_row.append(link(item.get("text", "Открыть"), app_deeplink(payload)))
                else:
                    new_row.append(item)
            rows.append(new_row)
        converted.append({"type": "inline_keyboard", "payload": {"buttons": rows}})
    return converted


class MaxApi:
    def __init__(self, session, token: str):
        self.session = session
        self.token = token
        self.headers = {"Authorization": token, "Content-Type": "application/json"}

    async def get(self, path: str, params=None):
        async with self.session.get(f"{API}{path}", headers=self.headers, params=params) as resp:
            text = await resp.text()
            if resp.status >= 400:
                logging.error("MAX GET %s -> %s %s", path, resp.status, text[:500])
                raise RuntimeError(f"GET {path} {resp.status}: {text[:300]}")
            return json.loads(text) if text else {}

    async def post(self, path: str, params=None, json_body=None):
        async with self.session.post(
            f"{API}{path}", headers=self.headers, params=params, json=json_body
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                logging.error("MAX POST %s -> %s %s", path, resp.status, text[:500])
                raise RuntimeError(f"POST {path} {resp.status}: {text[:400]}")
            return json.loads(text) if text else {}

    async def patch(self, path: str, json_body=None):
        async with self.session.patch(f"{API}{path}", headers=self.headers, json=json_body) as resp:
            text = await resp.text()
            if resp.status >= 400:
                logging.error("MAX PATCH %s -> %s %s", path, resp.status, text[:500])
                raise RuntimeError(f"PATCH {path} {resp.status}: {text[:300]}")
            return json.loads(text) if text else {}

    async def _post_cdn(self, url: str, path: str) -> dict:
        import aiohttp

        with open(path, "rb") as fh:
            content = fh.read()
        lower = path.lower()
        if lower.endswith(".mp4"):
            mime = "video/mp4"
        elif lower.endswith(".gif"):
            mime = "image/gif"
        elif lower.endswith((".jpg", ".jpeg")):
            mime = "image/jpeg"
        elif lower.endswith(".png"):
            mime = "image/png"
        else:
            mime = "application/octet-stream"
        last_error = None
        for label, ssl_ctx in (("public-ca", ssl.create_default_context()), ("insecure", unverified_ssl())):
            try:
                timeout = aiohttp.ClientTimeout(total=60)
                connector = aiohttp.TCPConnector(ssl=ssl_ctx)
                form = aiohttp.FormData()
                form.add_field("data", content, filename=os.path.basename(path), content_type=mime)
                async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                    async with session.post(url, data=form) as uploaded:
                        body = await uploaded.text()
                        logging.info("MAX CDN %s %s -> %s %s", label, path, uploaded.status, body[:180])
                        if uploaded.status >= 400:
                            last_error = RuntimeError(f"CDN {uploaded.status}: {body[:300]}")
                            continue
                        if not body:
                            return {}
                        try:
                            return json.loads(body)
                        except json.JSONDecodeError:
                            return {"raw": body}
            except Exception as e:
                last_error = e
                logging.warning("MAX CDN %s ошибка %s: %s", label, path, e)
        if last_error:
            raise last_error
        return {}

    async def upload_file(self, path: str, media_type: str) -> str:
        cached = load_media_cache()
        key = f"{media_type}:{os.path.abspath(path)}"
        if cached.get(key):
            return cached[key]
        auth = {"Authorization": self.token}
        async with self.session.post(f"{API}/uploads", headers=auth, params={"type": media_type}) as resp:
            raw = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"uploads {resp.status}: {raw[:300]}")
            init = json.loads(raw) if raw else {}
        logging.info("MAX /uploads %s -> %s", media_type, str(init)[:240])
        url = init.get("url")
        token = extract_upload_token(init)
        if not url:
            raise RuntimeError(f"uploads без url: {init}")
        parsed = await self._post_cdn(url, path)
        token = extract_upload_token(parsed) or token
        if not token:
            raise RuntimeError(f"нет token после загрузки {path}: {parsed}")
        cached[key] = token
        save_media_cache(cached)
        logging.info("MAX загрузил %s как %s", path, media_type)
        return token

    async def gif_att(self, mp4: str) -> dict | None:
        gif = ensure_loop_gif(mp4)
        if not gif:
            logging.error("MAX нет гифки для %s", mp4)
            return None
        for attempt in range(2):
            try:
                token = await asyncio.wait_for(self.upload_file(gif, "image"), timeout=45)
                return {"type": "image", "payload": {"token": token}}
            except Exception:
                logging.exception("не удалось загрузить гиф %s попытка %s", gif, attempt + 1)
                cached = load_media_cache()
                cached.pop(f"image:{os.path.abspath(gif)}", None)
                save_media_cache(cached)
        return None

    async def image_att(self, filename: str | None = None, amino: str | None = None, index: int = 0) -> dict | None:
        local = None
        if amino:
            local = find_rune_image(amino, index)
        if not local and filename:
            candidate = os.path.join("images", "runes", filename)
            if os.path.exists(candidate):
                local = candidate
        url = rune_page_url(amino, index) if amino else None
        if not url and filename:
            url = f"{PAGES}/images/runes/{urllib.parse.quote(filename)}"
        if url:
            return {"type": "image", "payload": {"url": url}}
        if local:
            try:
                token = await asyncio.wait_for(self.upload_file(local, "image"), timeout=20)
                return {"type": "image", "payload": {"token": token}}
            except Exception:
                logging.exception("загрузка картинки %s", local)
        return None

    async def _try_post(self, dests: list, body: dict):
        last_error = None
        not_ready = False
        for params in dests:
            try:
                return await self.post("/messages", params=params, json_body=body), None, False
            except Exception as e:
                last_error = e
                logging.warning("MAX send %s: %s", params, e)
                if is_not_ready(e):
                    not_ready = True
                    break
        return None, last_error, not_ready

    async def send(self, user_id: int, text: str, attachments=None, chat_id=None, media=None):
        chat_id = chat_id or current_chat_id.get()
        dests = []
        if chat_id:
            dests.append({"chat_id": chat_id})
        dests.append({"user_id": user_id})
        media_list = [item for item in (media or []) if item]
        kb_list = list(attachments or [])
        variants = []
        if media_list and kb_list:
            variants.append(media_list + kb_list)
        if media_list:
            variants.append(media_list)
        if kb_list:
            variants.append(kb_list)
        if has_open_app(kb_list):
            variants.append(demote_open_app(kb_list))
        variants.append(None)
        last_error = None
        for atts in variants:
            body = {"text": text}
            if atts:
                body["attachments"] = atts
            retries = 4 if media_list and atts and any(a.get("type") in {"video", "image"} for a in atts) else 1
            for attempt in range(retries):
                if attempt:
                    await asyncio.sleep(2 * attempt)
                    logging.warning("MAX повтор отправки вложения, попытка %s", attempt + 1)
                result, last_error, not_ready = await self._try_post(dests, body)
                if result is not None:
                    return result
                if not_ready:
                    continue
                break
        logging.error("MAX не смог отправить сообщение user=%s: %s", user_id, last_error)
        if last_error:
            raise last_error

    async def answer(self, callback_id: str, notification: str | None = None):
        if not callback_id:
            return
        try:
            body = {}
            if notification:
                body["notification"] = notification
            await self.post("/answers", params={"callback_id": callback_id}, json_body=body)
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
    gif = await api.gif_att(GIF_START)
    await api.send(user_id, text, menu_kb(), media=[gif] if gif else None)


async def start_ritual(api: MaxApi, user_id: int):
    db = load_db()
    key = db_key(user_id)
    data = ensure_user_record(db, key)
    now = datetime.now()
    next_ritual = datetime.fromisoformat(data.get("next_ritual_time", now.isoformat()))
    if not ritual_unlimited(data) and now < next_ritual:
        left = next_ritual - now
        hours, rem = divmod(int(left.total_seconds()), 3600)
        minutes = rem // 60
        await api.send(
            user_id,
            f"⏳ Обряд уже проведен! Следующий через {hours} ч. {minutes} мин.\n"
            f"Пригласите друга по ссылке:\n{ref_link(user_id)}",
            kb(
                [{"type": "clipboard", "text": "📋 Скопировать ссылку", "payload": ref_link(user_id)}],
            ),
        )
        return
    data["ritual_step"] = 1
    data["last_active"] = now.isoformat()
    save_db(db)
    fsm_set(user_id, {"step": "blue", "complex": 1, "runes": [], "aminos": [], "images": []})
    gif = await api.gif_att(GIF_RITUAL)
    await api.send(
        user_id,
        "Бросай как на примере выше\n\n🔮 **Комплекс 1.** Брось палочки и посмотри на **синюю** грань. Сколько точек?",
        kb(throw_row("🔵")),
        media=[gif] if gif else None,
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
    photo = await api.image_att(amino=amino, index=i)
    caption = f"🧬 **{amino}**\n🔮 Руна: **{symbol}**"
    if not photo and url:
        rows.append([link("📷 Открыть картинку", url)])
    await api.send(user_id, caption, kb(*rows), media=[photo] if photo else None)


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
    schedule_next_ritual(data, now)
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
    photos = []
    for idx, amino_name in enumerate(aminos):
        att = await api.image_att(filename=images[idx] if idx < len(images) else None)
        if att:
            photos.append(att)
    result_payload = encode_result_payload(aminos, images)
    result_btn = [open_app("📖 Получить результаты", result_payload)]
    if now < trial_end:
        await api.send(
            user_id,
            f"🎉 **Обряд завершён!**\nТриада: **{triad}**\nОсталось дней: {days_left_from(trial_end, now)}",
            kb(result_btn, [link("🌐 Результаты в браузере", result_url)]),
            media=photos or None,
        )
    else:
        await api.send(
            user_id,
            f"🎉 **Обряд завершён!**\nТриада: **{triad}**\n\n"
            "Бесплатный период закончился. Выберите тариф — оплата через Telegram "
            "(счёт ЮKassa), доступ откроется и здесь, в MAX.",
            kb(
                [btn(f"💳 {SUB_PLANS[1]['label']} — {SUB_PLANS[1]['rub']} ₽", "pay_1")],
                [btn(f"💳 {SUB_PLANS[3]['label']} — {SUB_PLANS[3]['rub']} ₽", "pay_3")],
                [btn(f"💳 {SUB_PLANS[12]['label']} — {SUB_PLANS[12]['rub']} ₽", "pay_12")],
                result_btn,
                [link("🌐 Результаты в браузере", result_url)],
            ),
            media=photos or None,
        )


async def handle_callback(api: MaxApi, user_id: int, payload: str, callback_id: str):
    await api.answer(callback_id)
    if payload == "start_ritual":
        await start_ritual(api, user_id)
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
    if payload.startswith("pay_"):
        months = int(payload.split("_")[1])
        plan = SUB_PLANS.get(months, SUB_PLANS[1])
        url = f"https://t.me/{telegram_bot_name()}?start=mp_{user_id}_{months}"
        await api.send(
            user_id,
            f"Оплата: **{plan['label']}** — **{plan['rub']} ₽**\n\n"
            "MAX не выставляет свой счёт, поэтому оплата идёт в Telegram тем же ЮKassa. "
            "После оплаты доступ откроется и в MAX — напишите «начать».",
            kb([link(f"💳 Оплатить {plan['rub']} ₽", url)]),
        )
        return
    if payload.startswith("rune_"):
        st = fsm_get(user_id)
        await pick_rune(api, user_id, st, int(payload.split("_")[1]))
        return


def is_max_admin(user_id: int) -> bool:
    admin = env("MAX_ADMIN_ID")
    return bool(admin) and str(user_id) == admin


async def handle_text(api: MaxApi, user_id: int, text: str):
    raw = (text or "").strip()
    low = raw.lower().split("@")[0].strip()
    if low in {"/start", "start", "начать"}:
        await cmd_start(api, user_id)
        return
    if low.startswith("/grant"):
        if not is_max_admin(user_id):
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
    if low.startswith("/allow_ritual") or low.startswith("/обряд_лимит"):
        if not is_max_admin(user_id):
            return
        parts = raw.split()[1:]
        parsed = parse_ritual_admin_args(parts, str(user_id))
        if not parsed:
            await api.send(
                user_id,
                "Обряд:\n"
                "`/allow_ritual` — сбросить таймер себе\n"
                "`/allow_ritual inf` — безлимит себе\n"
                "`/allow_ritual off` — снова 12 часов\n"
                "`/allow_ritual <id> inf` — безлимит человеку\n"
                "`/allow_ritual <id> off` — выключить",
            )
            return
        target, mode = parsed
        key = db_key(int(target))
        db = load_db()
        data = set_ritual_mode(db, key, mode)
        save_db(db)
        if mode == "unlimited":
            msg = f"Безлимит обрядов включён для {target}."
        elif mode == "off":
            msg = f"Безлимит выключен для {target}."
        else:
            msg = f"Таймер обряда сброшен для {target}."
        await api.send(user_id, msg)
        return
    if "обряд" in low:
        await start_ritual(api, user_id)
        return
    await cmd_start(api, user_id)


def extract_ids(update: dict) -> tuple[int | None, int | None]:
    user_id = None
    chat_id = update.get("chat_id")
    if isinstance(update.get("user"), dict):
        user_id = update["user"].get("user_id")
    cb = update.get("callback") or {}
    if isinstance(cb, dict) and isinstance(cb.get("user"), dict):
        user_id = user_id or cb["user"].get("user_id")
    msg = update.get("message") or {}
    sender = msg.get("sender") or {}
    user_id = user_id or sender.get("user_id")
    rec = msg.get("recipient") or {}
    chat_id = rec.get("chat_id") or chat_id
    user_id = user_id or rec.get("user_id")
    return user_id, chat_id


async def handle_update(api: MaxApi, update: dict):
    utype = update.get("update_type")
    user_id, chat_id = extract_ids(update)
    logging.info("MAX update %s user=%s chat=%s", utype, user_id, chat_id)
    if not user_id and chat_id:
        user_id = chat_id
    if not user_id:
        logging.info("skip update without user: %s %s", utype, list(update.keys()))
        return
    token = current_chat_id.set(chat_id)
    try:
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
            return
        if utype in {"bot_added", "dialog_unmuted"}:
            await cmd_start(api, user_id)
    finally:
        current_chat_id.reset(token)


async def wait_for_token() -> str:
    while True:
        token = max_token()
        if token:
            logging.info(
                "MAX токен найден в %s, длина %s",
                ENV_PATH,
                len(token),
            )
            return token
        exists = os.path.exists(ENV_PATH)
        logging.warning(
            "MAX_BOT_TOKEN не найден (файл .env %s). Жду 20 сек. Путь: %s",
            "есть" if exists else "НЕТ",
            ENV_PATH,
        )
        await asyncio.sleep(20)


async def main():
    import aiohttp

    token = await wait_for_token()
    timeout = aiohttp.ClientTimeout(total=90)
    ssl_ctx = await max_ssl_context()
    last_error = None
    for attempt, ctx in enumerate((ssl_ctx, unverified_ssl()), start=1):
        connector = aiohttp.TCPConnector(ssl=ctx)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            api = MaxApi(session, token)
            try:
                me = await api.get("/me")
            except Exception as e:
                last_error = e
                if attempt == 1 and "CERTIFICATE" in str(e).upper():
                    logging.warning("Сертификат MAX не принят системой, повтор без проверки SSL")
                    continue
                logging.error(
                    "MAX не принял токен или API недоступен: %s. Файл %s",
                    e,
                    ENV_PATH,
                )
                raise
            logging.info("MAX бот онлайн: @%s id=%s", me.get("username"), me.get("user_id"))
            BOT_META["username"] = (me.get("username") or "").lstrip("@")
            BOT_META["user_id"] = me.get("user_id")
            if env("MAX_BOT_USERNAME"):
                BOT_META["username"] = env("MAX_BOT_USERNAME").lstrip("@")
            for gif_path in (GIF_START, GIF_RITUAL):
                att = await api.gif_att(gif_path)
                logging.info("MAX гиф %s: %s", gif_path, "готово" if att else "нет")
            try:
                await api.patch("/me/commands", json_body={
                    "commands": [
                    {"name": "start", "description": "Запуск и меню"},
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
                    params = {"timeout": 30, "limit": 100}
                    if marker is not None:
                        params["marker"] = marker
                    data = await api.get("/updates", params=params)
                    updates = data.get("updates") or []
                    if updates:
                        logging.info("MAX получил %s событий", len(updates))
                    marker = data.get("marker", marker)
                    if marker is not None:
                        with open(MARKER_FILE, "w") as f:
                            f.write(str(marker))
                    for upd in updates:
                        try:
                            await handle_update(api, upd)
                        except Exception:
                            logging.exception("ошибка обработки MAX update")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logging.exception("MAX long poll")
                    await asyncio.sleep(3)
            return
    if last_error:
        raise last_error


if __name__ == "__main__":
    asyncio.run(main())
