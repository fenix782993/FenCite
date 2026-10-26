import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID_RAW = os.getenv("OWNER_ID", "").strip()
DB_PATH = Path(os.getenv("DB_PATH", "data/aliant_media.db"))
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH

if not BOT_TOKEN or ":" not in BOT_TOKEN:
    raise RuntimeError("Укажи действительный BOT_TOKEN в файле .env")
if not OWNER_ID_RAW.isdigit():
    raise RuntimeError("Укажи свой числовой OWNER_ID в файле .env")

OWNER_ID = int(OWNER_ID_RAW)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("aliant-media")

import sqlite3

def connect():
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER NOT NULL,
            added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            age TEXT NOT NULL,
            game_nick TEXT NOT NULL,
            discord TEXT NOT NULL,
            vk TEXT NOT NULL,
            channel TEXT NOT NULL,
            purpose TEXT NOT NULL,
            telegram_contact TEXT NOT NULL,
            experience TEXT NOT NULL,
            extra TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            decided_at TEXT,
            decided_by INTEGER,
            decision_reason TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_app_status
            ON applications(status);
        CREATE INDEX IF NOT EXISTS idx_app_user
            ON applications(user_id);
        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            details TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)
        db.commit()

def audit(actor, action, details):
    with connect() as db:
        db.execute(
            "INSERT INTO audit(actor_id, action, details, created_at) "
            "VALUES (?, ?, ?, ?)",
            (actor, action, str(details)[:1500], now())
        )
        db.commit()

def is_admin(user_id):
    if user_id == OWNER_ID:
        return True
    with connect() as db:
        return db.execute(
            "SELECT 1 FROM admins WHERE user_id=?", (user_id,)
        ).fetchone() is not None

def get_app(app_id):
    with connect() as db:
        return db.execute(
            "SELECT * FROM applications WHERE id=?", (app_id,)
        ).fetchone()

def app_text(a):
    return (
        f"📨 <b>ЗАЯВКА ALIANT MEDIA №{a['id']}</b>\n\n"
        f"👤 <b>Возраст:</b> {esc(a['age'])}\n"
        f"🎮 <b>Игровой ник:</b> {esc(a['game_nick'])}\n"
        f"💬 <b>Discord:</b> {esc(a['discord'])}\n"
        f"🔗 <b>VK:</b> {esc(a['vk'])}\n"
        f"📺 <b>Канал:</b> {esc(a['channel'])}\n"
        f"🎯 <b>Цель:</b> {esc(a['purpose'])}\n"
        f"📱 <b>Telegram для связи:</b> {esc(a['telegram_contact'])}\n"
        f"🗓 <b>Опыт публикаций:</b> {esc(a['experience'])}\n"
        f"📝 <b>Дополнительно:</b> {esc(a['extra'] or 'Не указано')}\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{a['user_id']}</code>\n"
        f"🔖 <b>Username:</b> @{esc(a['username'] or 'не указан')}\n"
        f"⏰ <b>Дата:</b> {esc(a['created_at'])}\n"
        f"📌 <b>Статус:</b> {esc(a['status'])}"
    )

def esc(value):
    s = str(value or "")
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))

def admin_keyboard(app_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Одобрить",
                callback_data=f"app:approve:{app_id}"
            ),
            InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"app:reject:{app_id}"
            )
        ],
        [InlineKeyboardButton(
            text="👤 Профиль заявителя",
            callback_data=f"app:profile:{app_id}"
        )]
    ])

class ApplicationForm(StatesGroup):
    age = State()
    game_nick = State()
    discord = State()
    vk = State()
    channel = State()
    purpose = State()
    telegram_contact = State()
    experience = State()
    extra = State()

class RejectForm(StatesGroup):
    reason = State()

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

CRITERIA = (
    "📋 <b>ТРЕБОВАНИЯ ALIANT MEDIA</b>\n\n"
    "• Создавать и публиковать контент не менее одного месяца.\n"
    "• Поддерживать регулярную активность.\n"
    "• Вести себя адекватно и уважительно.\n"
    "• Не допускать токсичности, оскорблений и травли.\n"
    "• Публиковать релевантный контент по проекту.\n"
    "• Не выдавать чужие материалы за собственные.\n"
    "• Указывать правдивые сведения.\n\n"
    "Администрация проверяет соблюдение требований вручную. "
    "Подача заявки не гарантирует одобрения."
)

def rules_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Ознакомился, подать заявку",
            callback_data="form:start"
        )],
        [InlineKeyboardButton(
            text="↩️ Отмена",
            callback_data="form:cancel"
        )]
    ])

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📥 Ожидающие заявки",
                callback_data="menu:pending"
            ),
            InlineKeyboardButton(
                text="📊 Статистика",
                callback_data="menu:stats"
            )
        ],
        [
            InlineKeyboardButton(
                text="🏆 Партнёры",
                callback_data="menu:partners"
            ),
            InlineKeyboardButton(
                text="📜 Журнал",
                callback_data="menu:logs"
            )
        ],
        [InlineKeyboardButton(
            text="👮 Администраторы",
            callback_data="menu:admins"
        )]
    ])

async def safe_send(user_id, text, **kwargs):
    try:
        return await bot.send_message(user_id, text, **kwargs)
    except Exception:
        log.exception("Не удалось отправить сообщение пользователю %s", user_id)
        return None

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    text = (
        "🔴 <b>ALIANT MEDIA</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Добро пожаловать в программу медиапартнёров Aliant!\n\n"
        "Здесь ты можешь подать заявку на сотрудничество, "
        "указать свои площадки и рассказать о своём контенте.\n\n"
        "📌 Перед подачей внимательно ознакомься с требованиями.\n\n"
        "Используй /apply для подачи заявки.\n"
        "Используй /help для списка команд."
    )
    if message.from_user.id == OWNER_ID:
        text += "\n\n👑 Ты владелец бота. Команды управления: /admin"
    await message.answer(text)

@dp.message(Command("help"))
async def help_cmd(message: Message):
    text = (
        "<b>КОМАНДЫ ALIANT MEDIA</b>\n\n"
        "/start — главное меню\n"
        "/apply — подать заявку\n"
        "/cancel — отменить заполнение\n"
        "/help — помощь"
    )
    if is_admin(message.from_user.id):
        text += (
            "\n\n<b>Команды администрации</b>\n"
            "/admin — панель управления\n"
            "/pending — ожидающие заявки\n"
            "/stats — статистика\n"
            "/partners — список партнёров\n"
            "/logs — последние действия\n"
        )
    if message.from_user.id == OWNER_ID:
        text += (
            "\n<b>Команды владельца</b>\n"
            "/addadmin ID — добавить администратора\n"
            "/deladmin ID — снять администратора\n"
            "/admins — список администраторов"
        )
    await message.answer(text)

@dp.message(Command("apply"))
async def apply(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(CRITERIA, reply_markup=rules_keyboard())

@dp.callback_query(F.data == "form:start")
async def form_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ApplicationForm.age)
    await callback.message.answer(
        "Шаг 1/9. Сколько тебе лет? Введи число."
    )

@dp.callback_query(F.data == "form:cancel")
async def form_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Заполнение отменено")
    await callback.message.answer("Заполнение заявки отменено.")

@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено. /apply — начать заново.")

async def ask_next(message, state, field, prompt, next_state):
    value = (message.text or "").strip()
    if not value:
        await message.answer("Ответ не должен быть пустым. Попробуй ещё раз.")
        return
    if len(value) > 1000:
        await message.answer("Слишком длинный ответ. Максимум 1000 символов.")
        return
    await state.update_data(**{field: value})
    await state.set_state(next_state)
    await message.answer(prompt)

@dp.message(ApplicationForm.age)
async def form_age(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not re.fullmatch(r"\d{1,2}", value) or not 10 <= int(value) <= 99:
        await message.answer("Укажи возраст числом от 10 до 99.")
        return
    await state.update_data(age=value)
    await state.set_state(ApplicationForm.game_nick)
    await message.answer("Шаг 2/9. Укажи игровой никнейм.")

@dp.message(ApplicationForm.game_nick)
async def form_nick(message: Message, state: FSMContext):
    await ask_next(message, state, "game_nick",
                   "Шаг 3/9. Укажи Discord username.",
                   ApplicationForm.discord)

@dp.message(ApplicationForm.discord)
async def form_discord(message: Message, state: FSMContext):
    await ask_next(message, state, "discord",
                   "Шаг 4/9. Пришли ссылку на свой профиль VK.",
                   ApplicationForm.vk)

@dp.message(ApplicationForm.vk)
async def form_vk(message: Message, state: FSMContext):
    await ask_next(message, state, "vk",
                   "Шаг 5/9. Пришли ссылку на канал или площадку, "
                   "где публикуешь контент.",
                   ApplicationForm.channel)

@dp.message(ApplicationForm.channel)
async def form_channel(message: Message, state: FSMContext):
    await ask_next(message, state, "channel",
                   "Шаг 6/9. С какой целью хочешь стать медиапартнёром?",
                   ApplicationForm.purpose)

@dp.message(ApplicationForm.purpose)
async def form_purpose(message: Message, state: FSMContext):
    await ask_next(message, state, "purpose",
                   "Шаг 7/9. Укажи Telegram для связи "
                   "(например, @username).",
                   ApplicationForm.telegram_contact)

@dp.message(ApplicationForm.telegram_contact)
async def form_contact(message: Message, state: FSMContext):
    await ask_next(message, state, "telegram_contact",
                   "Шаг 8/9. Как давно публикуешь контент? "
                   "Укажи срок и регулярность.",
                   ApplicationForm.experience)

@dp.message(ApplicationForm.experience)
async def form_experience(message: Message, state: FSMContext):
    await ask_next(message, state, "experience",
                   "Шаг 9/9. Дополнительная информация. "
                   "Если нечего добавить, напиши «Нет».",
                   ApplicationForm.extra)

@dp.message(ApplicationForm.extra)
async def form_extra(message: Message, state: FSMContext):
    extra = (message.text or "").strip()
    if not extra:
        await message.answer("Напиши текст или «Нет».")
        return
    if len(extra) > 1000:
        await message.answer("Максимум 1000 символов.")
        return

    data = await state.get_data()
    user = message.from_user
    with connect() as db:
        existing = db.execute(
            "SELECT id FROM applications WHERE user_id=? AND status='pending'",
            (user.id,)
        ).fetchone()
        if existing:
            await state.clear()
            await message.answer(
                f"У тебя уже есть заявка №{existing['id']} на рассмотрении."
            )
            return
        cur = db.execute("""
            INSERT INTO applications
            (user_id, username, age, game_nick, discord, vk, channel,
             purpose, telegram_contact, experience, extra, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (
            user.id, user.username, data["age"], data["game_nick"],
            data["discord"], data["vk"], data["channel"], data["purpose"],
            data["telegram_contact"], data["experience"],
            "" if extra.lower() == "нет" else extra, now()
        ))
        app_id = cur.lastrowid
        db.commit()

    await state.clear()
    audit(user.id, "application_created", f"Заявка №{app_id}")
    await message.answer(
        f"✅ <b>Заявка №{app_id} отправлена!</b>\n\n"
        "Администрация рассмотрит её и сообщит результат здесь. "
        "Не подавай повторную заявку, пока эта находится на рассмотрении."
    )

    a = get_app(app_id)
    text = app_text(a)
    with connect() as db:
        admins = [r["user_id"] for r in db.execute(
            "SELECT user_id FROM admins"
        ).fetchall()]
    recipients = set(admins)
    recipients.add(OWNER_ID)
    for admin_id in recipients:
        await safe_send(
            admin_id, text, reply_markup=admin_keyboard(app_id)
        )

async def require_admin_callback(callback):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return False
    return True

@dp.callback_query(F.data.startswith("app:"))
async def app_action(callback: CallbackQuery, state: FSMContext):
    if not await require_admin_callback(callback):
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer("Некорректная команда.")
        return
    action, app_id = parts[1], int(parts[2])
    a = get_app(app_id)
    if not a:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    if action == "profile":
        await callback.answer()
        await callback.message.answer(
            f"Профиль заявителя: <code>{a['user_id']}</code>\n"
            f"Username: @{esc(a['username'] or 'не указан')}\n"
            f"Игровой ник: {esc(a['game_nick'])}\n"
            f"Заявка №{app_id}"
        )
        return

    if a["status"] != "pending":
        await callback.answer("Эта заявка уже обработана.", show_alert=True)
        return

    if action == "approve":
        with connect() as db:
            db.execute("""
                UPDATE applications
                SET status='approved', decided_at=?, decided_by=?
                WHERE id=? AND status='pending'
            """, (now(), callback.from_user.id, app_id))
            db.commit()
        audit(callback.from_user.id, "application_approved", f"Заявка №{app_id}")
        await callback.answer("Заявка одобрена")
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"✅ Заявка №{app_id} одобрена администратором "
            f"<code>{callback.from_user.id}</code>."
        )
        await safe_send(
            a["user_id"],
            "🔴 <b>ALIANT MEDIA</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🎉 <b>ПОЗДРАВЛЯЕМ!</b>\n\n"
            f"Твоя заявка №{app_id} рассмотрена и одобрена!\n\n"
            f"🎮 Игровой ник: <b>{esc(a['game_nick'])}</b>\n"
            "🏆 Ты принят в программу медиапартнёров Aliant Media.\n\n"
            "Желаем успехов, качественного контента и развития!\n"
            f"Решение принял: <code>{callback.from_user.id}</code>"
        )
    elif action == "reject":
        await state.set_state(RejectForm.reason)
        await state.update_data(reject_app_id=app_id)
        await callback.answer()
        await callback.message.answer(
            f"Напиши причину отказа для заявки №{app_id} "
            "одним сообщением. Для отмены используй /cancel."
        )
    else:
        await callback.answer("Неизвестное действие.")

@dp.message(RejectForm.reason)
async def reject_reason(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    reason = (message.text or "").strip()
    if not reason or len(reason) > 1000:
        await message.answer("Укажи причину длиной от 1 до 1000 символов.")
        return
    data = await state.get_data()
    app_id = data.get("reject_app_id")
    a = get_app(app_id)
    if not a or a["status"] != "pending":
        await state.clear()
        await message.answer("Заявка уже обработана или не найдена.")
        return

    with connect() as db:
        db.execute("""
            UPDATE applications
            SET status='rejected', decided_at=?, decided_by=?,
                decision_reason=?
            WHERE id=? AND status='pending'
        """, (now(), message.from_user.id, reason, app_id))
        db.commit()
    audit(message.from_user.id, "application_rejected",
          f"Заявка №{app_id}; причина: {reason}")
    await state.clear()
    await message.answer(f"❌ Заявка №{app_id} отклонена.")
    await safe_send(
        a["user_id"],
        "🔴 <b>ALIANT MEDIA</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Заявка №{app_id} не была одобрена.\n\n"
        f"<b>Причина:</b> {esc(reason)}\n\n"
        "Спасибо за интерес к Aliant Media. Ты можешь улучшить "
        "контент и подать новую заявку после завершения рассмотрения."
    )

@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return
    await message.answer("🔴 <b>ALIANT MEDIA — ПАНЕЛЬ</b>",
                         reply_markup=admin_menu())

@dp.callback_query(F.data.startswith("menu:"))
async def menu_action(callback: CallbackQuery):
    if not await require_admin_callback(callback):
        return
    action = callback.data.split(":", 1)[1]
    await callback.answer()
    if action == "pending":
        await send_pending(callback.message.chat.id)
    elif action == "stats":
        await send_stats(callback.message.chat.id)
    elif action == "partners":
        await send_partners(callback.message.chat.id)
    elif action == "logs":
        await send_logs(callback.message.chat.id)
    elif action == "admins":
        await send_admins(callback.message.chat.id)
    else:
        await callback.message.answer("Неизвестный раздел.")

async def send_pending(chat_id):
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM applications WHERE status='pending' "
            "ORDER BY id DESC LIMIT 20"
        ).fetchall()
    if not rows:
        await safe_send(chat_id, "📥 Ожидающих заявок нет.")
        return
    for a in rows:
        await safe_send(chat_id, app_text(a),
                        reply_markup=admin_keyboard(a["id"]))

async def send_stats(chat_id):
    with connect() as db:
        total = db.execute("SELECT COUNT(*) n FROM applications").fetchone()["n"]
        pending = db.execute(
            "SELECT COUNT(*) n FROM applications WHERE status='pending'"
        ).fetchone()["n"]
        approved = db.execute(
            "SELECT COUNT(*) n FROM applications WHERE status='approved'"
        ).fetchone()["n"]
        rejected = db.execute(
            "SELECT COUNT(*) n FROM applications WHERE status='rejected'"
        ).fetchone()["n"]
    await safe_send(
        chat_id,
        "📊 <b>СТАТИСТИКА ALIANT MEDIA</b>\n\n"
        f"Всего заявок: {total}\n"
        f"На рассмотрении: {pending}\n"
        f"Одобрено: {approved}\n"
        f"Отклонено: {rejected}"
    )

async def send_partners(chat_id):
    with connect() as db:
        rows = db.execute(
            "SELECT id, user_id, game_nick, decided_at "
            "FROM applications WHERE status='approved' "
            "ORDER BY decided_at DESC LIMIT 50"
        ).fetchall()
    if not rows:
        await safe_send(chat_id, "🏆 Одобренных медиапартнёров пока нет.")
        return
    lines = ["🏆 <b>МЕДИАПАРТНЁРЫ</b>\n"]
    for a in rows:
        lines.append(
            f"• {esc(a['game_nick'])} — заявка №{a['id']}, "
            f"ID <code>{a['user_id']}</code>"
        )
    await safe_send(chat_id, "\n".join(lines))

async def send_logs(chat_id):
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM audit ORDER BY id DESC LIMIT 20"
        ).fetchall()
    if not rows:
        await safe_send(chat_id, "📜 Журнал пока пуст.")
        return
    lines = ["📜 <b>ПОСЛЕДНИЕ ДЕЙСТВИЯ</b>\n"]
    for r in rows:
        lines.append(
            f"#{r['id']} | {esc(r['created_at'])}\n"
            f"ID: <code>{r['actor_id']}</code> | "
            f"{esc(r['action'])}\n{esc(r['details'])}\n"
        )
    await safe_send(chat_id, "\n".join(lines))

async def send_admins(chat_id):
    with connect() as db:
        rows = db.execute(
            "SELECT user_id, added_by, added_at FROM admins ORDER BY user_id"
        ).fetchall()
    lines = ["👮 <b>АДМИНИСТРАТОРЫ</b>\n",
             f"👑 Владелец: <code>{OWNER_ID}</code>"]
    if rows:
        for r in rows:
            lines.append(
                f"• <code>{r['user_id']}</code> "
                f"(добавил {r['added_by']})"
            )
    else:
        lines.append("Дополнительных администраторов нет.")
    await safe_send(chat_id, "\n".join(lines))

@dp.message(Command("pending"))
async def pending_cmd(message: Message):
    if is_admin(message.from_user.id):
        await send_pending(message.chat.id)
    else:
        await message.answer("Доступ запрещён.")

@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    if is_admin(message.from_user.id):
        await send_stats(message.chat.id)
    else:
        await message.answer("Доступ запрещён.")

@dp.message(Command("partners"))
async def partners_cmd(message: Message):
    if is_admin(message.from_user.id):
        await send_partners(message.chat.id)
    else:
        await message.answer("Доступ запрещён.")

@dp.message(Command("logs"))
async def logs_cmd(message: Message):
    if is_admin(message.from_user.id):
        await send_logs(message.chat.id)
    else:
        await message.answer("Доступ запрещён.")

@dp.message(Command("admins"))
async def admins_cmd(message: Message):
    if message.from_user.id == OWNER_ID:
        await send_admins(message.chat.id)
    else:
        await message.answer("Только владелец может управлять администраторами.")

@dp.message(Command("addadmin"))
async def add_admin_cmd(message: Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("Только владелец может добавлять администраторов.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /addadmin TELEGRAM_ID")
        return
    uid = int(parts[1])
    if uid == OWNER_ID:
        await message.answer("Владелец уже имеет полный доступ.")
        return
    with connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO admins(user_id, added_by, added_at) "
            "VALUES (?, ?, ?)", (uid, OWNER_ID, now())
        )
        db.commit()
    audit(OWNER_ID, "admin_added", f"Добавлен администратор {uid}")
    await message.answer(f"✅ Администратор <code>{uid}</code> добавлен.")

@dp.message(Command("deladmin"))
async def del_admin_cmd(message: Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("Только владелец может снимать администраторов.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /deladmin TELEGRAM_ID")
        return
    uid = int(parts[1])
    with connect() as db:
        cur = db.execute("DELETE FROM admins WHERE user_id=?", (uid,))
        db.commit()
    audit(OWNER_ID, "admin_removed", f"Удалён администратор {uid}")
    if cur.rowcount:
        await message.answer(f"✅ Администратор <code>{uid}</code> снят.")
    else:
        await message.answer("Такого администратора в списке нет.")

async def main():
    init_db()
    audit(OWNER_ID, "bot_started", "Бот запущен")
    log.info("Aliant Media запускается; база: %s", DB_PATH)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Бот остановлен")
