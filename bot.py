
import asyncio
import html
import logging
import os
import sqlite3
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    FSInputFile,
)
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID_RAW = os.getenv("OWNER_ID", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN")

if not OWNER_ID_RAW.isdigit():
    raise RuntimeError("OWNER_ID должен быть числовым Telegram ID")

OWNER_ID = int(OWNER_ID_RAW)
DB_PATH = Path(os.getenv("DB_PATH", "data/aliant_media.db"))
LOGO_PATH = Path(os.getenv("LOGO_PATH", "logo.png"))

DB_PATH.parent.mkdir(parents=True, exist_ok=True)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()


# =========================================================
# БАЗА ДАННЫХ
# =========================================================

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER NOT NULL,
            added_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT,
            age TEXT NOT NULL,
            game_nick TEXT NOT NULL,
            discord TEXT NOT NULL,
            vk TEXT NOT NULL,
            channel TEXT NOT NULL,
            purpose TEXT NOT NULL,
            telegram_contact TEXT NOT NULL,
            content_time TEXT NOT NULL,
            extra TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            reviewed_at INTEGER,
            reviewed_by INTEGER,
            review_reason TEXT
        );

        CREATE TABLE IF NOT EXISTS partners (
            user_id INTEGER PRIMARY KEY,
            application_id INTEGER NOT NULL,
            game_nick TEXT NOT NULL,
            username TEXT,
            added_at INTEGER NOT NULL,
            added_by INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            details TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        """)

        con.execute(
            """
            INSERT OR IGNORE INTO admins(user_id, added_by, added_at)
            VALUES (?, ?, ?)
            """,
            (OWNER_ID, OWNER_ID, int(time.time())),
        )


def audit(actor_id: int, action: str, details: str):
    with db() as con:
        con.execute(
            """
            INSERT INTO audit(actor_id, action, details, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (actor_id, action, details, int(time.time())),
        )


def is_admin(user_id: int) -> bool:
    with db() as con:
        return con.execute(
            "SELECT 1 FROM admins WHERE user_id = ?",
            (user_id,),
        ).fetchone() is not None


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


def esc(value) -> str:
    return html.escape(str(value if value is not None else "—"))


def format_time(timestamp) -> str:
    if not timestamp:
        return "—"
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(timestamp))


# =========================================================
# КЛАВИАТУРЫ
# =========================================================

def user_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="👤 Профиль"),
                KeyboardButton(text="📝 Подать заявку"),
            ]
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие",
    )


def admin_keyboard():
    rows = [
        [InlineKeyboardButton(
            text="📨 Заявки на рассмотрении",
            callback_data="admin:pending",
        )],
        [InlineKeyboardButton(
            text="🤝 Медиа-партнёры",
            callback_data="admin:partners",
        )],
        [InlineKeyboardButton(
            text="📊 Статистика",
            callback_data="admin:stats",
        )],
        [InlineKeyboardButton(
            text="📒 Журнал действий",
            callback_data="admin:logs",
        )],
    ]

    if is_owner(OWNER_ID):
        rows.append([
            InlineKeyboardButton(
                text="🛡 Администраторы",
                callback_data="admin:admins",
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def application_keyboard(app_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Одобрить",
                callback_data=f"app:approve:{app_id}",
            ),
            InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"app:reject:{app_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="👤 Профиль заявителя",
                callback_data=f"app:profile:{app_id}",
            )
        ],
    ])


def partner_keyboard(user_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🗑 Удалить медиа",
                callback_data=f"partner:delete:{user_id}",
            )
        ]
    ])


# =========================================================
# ЛОГОТИП И СООБЩЕНИЯ
# =========================================================

async def send_brand(message: Message, text: str, reply_markup=None):
    """
    Отправляет логотип над текстом.
    В Telegram фотография и подпись находятся в одном сообщении.
    """
    if LOGO_PATH.is_file():
        try:
            await message.answer_photo(
                photo=FSInputFile(LOGO_PATH),
                caption=text,
                reply_markup=reply_markup,
            )
            return
        except Exception:
            logging.exception("Не удалось отправить логотип")

    await message.answer(text, reply_markup=reply_markup)


# =========================================================
# АНКЕТА
# =========================================================

class ApplicationForm(StatesGroup):
    age = State()
    game_nick = State()
    discord = State()
    vk = State()
    channel = State()
    purpose = State()
    telegram_contact = State()
    content_time = State()
    extra = State()


class RejectForm(StatesGroup):
    reason = State()


QUESTIONS = [
    (
        ApplicationForm.age,
        "🎂 <b>Сколько тебе лет?</b>",
        "Укажи возраст числом от 10 до 99.",
        "age",
    ),
    (
        ApplicationForm.game_nick,
        "🎮 <b>Твой игровой никнейм</b>",
        "Напиши точный игровой ник.",
        "game_nick",
    ),
    (
        ApplicationForm.discord,
        "💬 <b>Твой Discord</b>",
        "Напиши Discord username или укажи «нет».",
        "discord",
    ),
    (
        ApplicationForm.vk,
        "🔵 <b>Ссылка на профиль VK</b>",
        "Отправь ссылку на страницу ВКонтакте.",
        "vk",
    ),
    (
        ApplicationForm.channel,
        "📺 <b>Ссылка на канал</b>",
        "Отправь ссылку на канал или страницу с контентом.",
        "channel",
    ),
    (
        ApplicationForm.purpose,
        "🎯 <b>Зачем хочешь стать медиа-партнёром?</b>",
        "Расскажи о своих целях и планах.",
        "purpose",
    ),
    (
        ApplicationForm.telegram_contact,
        "✈️ <b>Telegram для связи</b>",
        "Напиши @username или «этот аккаунт».",
        "telegram_contact",
    ),
    (
        ApplicationForm.content_time,
        "🗓 <b>Как давно создаёшь контент?</b>",
        "Контент должен публиковаться не менее месяца.",
        "content_time",
    ),
    (
        ApplicationForm.extra,
        "📝 <b>Дополнительная информация</b>",
        "Напиши детали или укажи «нет».",
        "extra",
    ),
]


def template_text():
    return (
        "📋 <b>ШАБЛОН ЗАЯВКИ ALIANT MEDIA</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Скопируй блок ниже и заполни его.\n\n"
        "<pre>"
        "ЗАЯВКА ALIANT MEDIA\n"
        "1. Возраст:\n"
        "2. Игровой никнейм:\n"
        "3. Discord:\n"
        "4. Ссылка на профиль VK:\n"
        "5. Ссылка на канал:\n"
        "6. Цель вступления:\n"
        "7. Telegram для связи:\n"
        "8. Как давно создаю контент:\n"
        "9. Дополнительная информация:\n"
        "</pre>\n"
        "⚠️ <b>Требования:</b> регулярная активность, собственный контент, "
        "уважительное поведение и честные данные. Заявки проверяются вручную.\n\n"
        "Для пошаговой подачи нажми <b>«📝 Подать заявку»</b> или введи "
        "<code>/apply</code>."
    )


def application_text(row):
    username = (
        "@" + esc(row["username"])
        if row["username"]
        else "не указан"
    )

    return (
        f"🆕 <b>ЗАЯВКА ALIANT MEDIA №{row['id']}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Имя:</b> {esc(row['full_name'])}\n"
        f"🆔 <b>Telegram ID:</b> <code>{row['user_id']}</code>\n"
        f"✈️ <b>Username:</b> {username}\n"
        f"🎂 <b>Возраст:</b> {esc(row['age'])}\n"
        f"🎮 <b>Игровой ник:</b> {esc(row['game_nick'])}\n"
        f"💬 <b>Discord:</b> {esc(row['discord'])}\n"
        f"🔵 <b>VK:</b> {esc(row['vk'])}\n"
        f"📺 <b>Канал:</b> {esc(row['channel'])}\n"
        f"🎯 <b>Цель:</b> {esc(row['purpose'])}\n"
        f"✈️ <b>Контакт:</b> {esc(row['telegram_contact'])}\n"
        f"🗓 <b>Опыт:</b> {esc(row['content_time'])}\n"
        f"📝 <b>Дополнительно:</b> {esc(row['extra'])}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🕒 <b>Дата подачи:</b> {format_time(row['created_at'])}\n"
        f"📌 <b>Статус:</b> {esc(row['status'])}"
    )


async def begin_application(message: Message, state: FSMContext):
    if not message.from_user:
        return

    with db() as con:
        pending = con.execute(
            """
            SELECT id FROM applications
            WHERE user_id = ? AND status = 'pending'
            ORDER BY id DESC LIMIT 1
            """,
            (message.from_user.id,),
        ).fetchone()

    if pending:
        await message.answer(
            "⏳ <b>Твоя заявка уже на рассмотрении.</b>\n"
            "Дождись сообщения от администрации.",
            reply_markup=user_keyboard(),
        )
        return

    await state.clear()
    await state.set_state(ApplicationForm.age)

    await message.answer(
        "📝 <b>ЗАЯВКА ALIANT MEDIA · 1/9</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🎂 <b>Сколько тебе лет?</b>\n\n"
        "<i>Отправь только ответ на этот вопрос.</i>",
        reply_markup=user_keyboard(),
    )


async def save_answer(message: Message, state: FSMContext, field: str, next_index: int):
    value = (message.text or "").strip()

    if not value:
        await message.answer("⚠️ <b>Отправь ответ текстом.</b>")
        return

    if len(value) > 1500:
        await message.answer("⚠️ <b>Ответ слишком длинный.</b> Максимум 1500 символов.")
        return

    if field == "age":
        if not value.isdigit() or not 10 <= int(value) <= 99:
            await message.answer("⚠️ <b>Укажи возраст числом от 10 до 99.</b>")
            return

    await state.update_data(**{field: value})

    if next_index < len(QUESTIONS):
        state_obj, prompt, hint, _ = QUESTIONS[next_index]
        await state.set_state(state_obj)
        await message.answer(
            f"📝 <b>ЗАЯВКА ALIANT MEDIA · {next_index + 1}/9</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"{prompt}\n\n"
            f"<i>{hint}</i>",
            reply_markup=user_keyboard(),
        )
        return

    data = await state.get_data()
    await state.clear()

    now = int(time.time())

    with db() as con:
        cur = con.execute(
            """
            INSERT INTO applications (
                user_id, username, full_name,
                age, game_nick, discord, vk, channel,
                purpose, telegram_contact, content_time, extra,
                status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                message.from_user.id,
                message.from_user.username,
                message.from_user.full_name,
                data["age"],
                data["game_nick"],
                data["discord"],
                data["vk"],
                data["channel"],
                data["purpose"],
                data["telegram_contact"],
                data["content_time"],
                data["extra"],
                now,
            ),
        )
        app_id = cur.lastrowid
        row = con.execute(
            "SELECT * FROM applications WHERE id = ?",
            (app_id,),
        ).fetchone()
        admins = con.execute("SELECT user_id FROM admins").fetchall()

    audit(
        message.from_user.id,
        "Заявка подана",
        f"№{app_id}; ник {data['game_nick']}",
    )

    for admin in admins:
        try:
            await bot.send_message(
                admin["user_id"],
                application_text(row),
                reply_markup=application_keyboard(app_id),
            )
        except Exception:
            logging.exception(
                "Не удалось отправить заявку администратору %s",
                admin["user_id"],
            )

    await message.answer(
        "✅ <b>ЗАЯВКА УСПЕШНО ОТПРАВЛЕНА</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎮 <b>Игровой ник:</b> {esc(data['game_nick'])}\n"
        "📌 <b>Статус:</b> ожидает рассмотрения\n\n"
        "Администрация рассмотрит заявку. Решение и комментарий придут "
        "тебе в этот Telegram-чат.",
        reply_markup=user_keyboard(),
    )


# =========================================================
# СТАРТ И ПОЛЬЗОВАТЕЛЬСКОЕ МЕНЮ
# =========================================================

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()

    text = (
        "🔥 <b>ALIANT MEDIA</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Платформа заявок на медиа-партнёрство.\n\n"
        "🎬 <b>Создавай контент. Развивай проект. Становись частью Aliant.</b>\n\n"
        "📝 Нажми <b>«Подать заявку»</b> или используй <code>/apply</code>.\n"
        "👤 В разделе <b>«Профиль»</b> можно посмотреть свой статус.\n\n"
        "Результат рассмотрения придёт прямо сюда."
    )

    await send_brand(message, text, user_keyboard())


@dp.message(Command("apply"))
async def apply_command(message: Message, state: FSMContext):
    await begin_application(message, state)


@dp.message(Command("template"))
async def template_command(message: Message):
    await message.answer(template_text(), reply_markup=user_keyboard())


@dp.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❎ <b>Действие отменено.</b>",
        reply_markup=user_keyboard(),
    )


@dp.message(F.text == "👤 Профиль")
async def profile(message: Message):
    with db() as con:
        partner = con.execute(
            "SELECT * FROM partners WHERE user_id = ?",
            (message.from_user.id,),
        ).fetchone()

        latest = con.execute(
            """
            SELECT * FROM applications
            WHERE user_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (message.from_user.id,),
        ).fetchone()

    if partner:
        status = "✅ <b>Медиа-партнёр</b>"
    elif latest and latest["status"] == "pending":
        status = "⏳ <b>Заявка на рассмотрении</b>"
    elif latest and latest["status"] == "rejected":
        status = "❌ <b>Заявка отклонена</b>"
    elif latest and latest["status"] == "approved":
        status = "✅ <b>Заявка одобрена</b>"
    else:
        status = "📭 <b>Заявок пока нет</b>"

    username = (
        "@" + esc(message.from_user.username)
        if message.from_user.username
        else "не указан"
    )

    await message.answer(
        "👤 <b>ТВОЙ ПРОФИЛЬ · ALIANT MEDIA</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>Telegram ID:</b> <code>{message.from_user.id}</code>\n"
        f"✈️ <b>Username:</b> {username}\n"
        f"📌 <b>Статус:</b> {status}\n"
        + (
            f"🎮 <b>Игровой ник:</b> {esc(partner['game_nick'])}\n"
            if partner
            else (
                f"🎮 <b>Последний ник:</b> {esc(latest['game_nick'])}\n"
                if latest
                else ""
            )
        ),
        reply_markup=user_keyboard(),
    )


@dp.message(F.text == "📝 Подать заявку")
async def apply_button(message: Message, state: FSMContext):
    await begin_application(message, state)


# =========================================================
# ОБРАБОТЧИКИ ОТВЕТОВ АНКЕТЫ
# =========================================================

@dp.message(ApplicationForm.age)
async def answer_age(message: Message, state: FSMContext):
    await save_answer(message, state, "age", 1)


@dp.message(ApplicationForm.game_nick)
async def answer_nick(message: Message, state: FSMContext):
    await save_answer(message, state, "game_nick", 2)


@dp.message(ApplicationForm.discord)
async def answer_discord(message: Message, state: FSMContext):
    await save_answer(message, state, "discord", 3)


@dp.message(ApplicationForm.vk)
async def answer_vk(message: Message, state: FSMContext):
    await save_answer(message, state, "vk", 4)


@dp.message(ApplicationForm.channel)
async def answer_channel(message: Message, state: FSMContext):
    await save_answer(message, state, "channel", 5)


@dp.message(ApplicationForm.purpose)
async def answer_purpose(message: Message, state: FSMContext):
    await save_answer(message, state, "purpose", 6)


@dp.message(ApplicationForm.telegram_contact)
async def answer_contact(message: Message, state: FSMContext):
    await save_answer(message, state, "telegram_contact", 7)


@dp.message(ApplicationForm.content_time)
async def answer_content_time(message: Message, state: FSMContext):
    await save_answer(message, state, "content_time", 8)


@dp.message(ApplicationForm.extra)
async def answer_extra(message: Message, state: FSMContext):
    await save_answer(message, state, "extra", 9)


# =========================================================
# АДМИН-ПАНЕЛЬ И РАССМОТРЕНИЕ ЗАЯВОК
# =========================================================

@dp.message(Command("admin"))
async def admin_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ <b>У тебя нет доступа к панели управления.</b>")
        return

    await message.answer(
        "🛡 <b>ALIANT MEDIA · ПАНЕЛЬ УПРАВЛЕНИЯ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Выбери нужный раздел:",
        reply_markup=admin_keyboard(),
    )


@dp.callback_query(F.data.startswith("app:"))
async def application_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    _, action, raw_id = callback.data.split(":")
    app_id = int(raw_id)

    with db() as con:
        row = con.execute(
            "SELECT * FROM applications WHERE id = ?",
            (app_id,),
        ).fetchone()

    if not row:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    if action == "profile":
        await callback.message.answer(
            "👤 <b>ПРОФИЛЬ ЗАЯВИТЕЛЯ</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🎮 <b>Игровой ник:</b> {esc(row['game_nick'])}\n"
            f"🆔 <b>Telegram ID:</b> <code>{row['user_id']}</code>\n"
            f"✈️ <b>Username:</b> @{esc(row['username']) if row['username'] else 'не указан'}\n"
            f"🕒 <b>Подана:</b> {format_time(row['created_at'])}\n"
            f"📌 <b>Статус:</b> {esc(row['status'])}"
        )
        await callback.answer()
        return

    if row["status"] != "pending":
        await callback.answer("Заявка уже рассмотрена", show_alert=True)
        return

    if action == "approve":
        now = int(time.time())

        with db() as con:
            con.execute(
                """
                UPDATE applications
                SET status = 'approved', reviewed_at = ?, reviewed_by = ?
                WHERE id = ?
                """,
                (now, callback.from_user.id, app_id),
            )

            con.execute(
                """
                INSERT INTO partners(
                    user_id, application_id, game_nick,
                    username, added_at, added_by
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    application_id = excluded.application_id,
                    game_nick = excluded.game_nick,
                    username = excluded.username,
                    added_at = excluded.added_at,
                    added_by = excluded.added_by
                """,
                (
                    row["user_id"],
                    app_id,
                    row["game_nick"],
                    row["username"],
                    now,
                    callback.from_user.id,
                ),
            )

        audit(
            callback.from_user.id,
            "Заявка одобрена",
            f"№{app_id}; {row['game_nick']}",
        )

        try:
            await bot.send_message(
                row["user_id"],
                "🎉 <b>ПОЗДРАВЛЯЕМ! ТЫ ОДОБРЕН В ALIANT MEDIA</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"🎮 <b>Игровой ник:</b> {esc(row['game_nick'])}\n"
                "🤝 <b>Статус:</b> медиа-партнёр Aliant\n\n"
                "Твоя заявка рассмотрена и одобрена администрацией.\n\n"
                "Добро пожаловать в команду <b>ALIANT MEDIA</b>! Создавай "
                "качественный контент, соблюдай правила проекта и поддерживай "
                "активность.\n\n"
                f"🛡 <b>Решение принял:</b> {esc(callback.from_user.full_name)}"
            )
        except Exception:
            logging.exception("Не удалось уведомить пользователя об одобрении")

        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"✅ <b>Заявка №{app_id} одобрена.</b>\n"
            "Пользователь уведомлён."
        )
        await callback.answer("Заявка одобрена")
        return

    if action == "reject":
        await state.set_state(RejectForm.reason)
        await state.update_data(reject_app_id=app_id)
        await callback.message.answer(
            f"✍️ <b>ОТКЛОНЕНИЕ ЗАЯВКИ №{app_id}</b>\n\n"
            "Напиши причину отказа одним сообщением.\n"
            "Она будет отправлена заявителю."
        )
        await callback.answer()


@dp.message(RejectForm.reason)
async def reject_reason(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    reason = (message.text or "").strip()

    if not reason:
        await message.answer("⚠️ <b>Укажи причину текстом.</b>")
        return

    data = await state.get_data()
    app_id = data.get("reject_app_id")

    with db() as con:
        row = con.execute(
            "SELECT * FROM applications WHERE id = ?",
            (app_id,),
        ).fetchone()

        if not row or row["status"] != "pending":
            await state.clear()
            await message.answer("⚠️ <b>Заявка уже рассмотрена или не найдена.</b>")
            return

        con.execute(
            """
            UPDATE applications
            SET status = 'rejected', reviewed_at = ?,
                reviewed_by = ?, review_reason = ?
            WHERE id = ?
            """,
            (int(time.time()), message.from_user.id, reason, app_id),
        )

    audit(
        message.from_user.id,
        "Заявка отклонена",
        f"№{app_id}; причина: {reason}",
    )

    try:
        await bot.send_message(
            row["user_id"],
            "📩 <b>РЕЗУЛЬТАТ РАССМОТРЕНИЯ ALIANT MEDIA</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🎮 <b>Игровой ник:</b> {esc(row['game_nick'])}\n"
            "❌ <b>Решение:</b> заявка отклонена\n\n"
            f"📝 <b>Причина:</b> {esc(reason)}\n\n"
            "Ты можешь исправить недочёты и подать заявку повторно позже."
        )
    except Exception:
        logging.exception("Не удалось уведомить пользователя об отказе")

    await state.clear()
    await message.answer(
        f"❌ <b>Заявка №{app_id} отклонена.</b>\n"
        "Пользователь уведомлён.",
        reply_markup=user_keyboard(),
    )


# =========================================================
# РАЗДЕЛЫ АДМИН-ПАНЕЛИ
# =========================================================

@dp.callback_query(F.data.startswith("admin:"))
async def admin_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    action = callback.data.split(":", 1)[1]

    with db() as con:
        if action == "pending":
            rows = con.execute(
                """
                SELECT * FROM applications
                WHERE status = 'pending'
                ORDER BY created_at ASC
                """
            ).fetchall()

            await callback.message.answer(
                "📨 <b>ЗАЯВКИ НА РАССМОТРЕНИИ</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"Количество: <b>{len(rows)}</b>"
            )

            for row in rows:
                await callback.message.answer(
                    application_text(row),
                    reply_markup=application_keyboard(row["id"]),
                )

            if not rows:
                await callback.message.answer("📭 <b>Новых заявок нет.</b>")

        elif action == "partners":
            rows = con.execute(
                "SELECT * FROM partners ORDER BY added_at DESC"
            ).fetchall()

            await callback.message.answer(
                "🤝 <b>МЕДИА-ПАРТНЁРЫ ALIANT</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"Всего: <b>{len(rows)}</b>"
            )

            for row in rows:
                await callback.message.answer(
                    "🤝 <b>МЕДИА-ПАРТНЁР</b>\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    f"🎮 <b>Ник:</b> {esc(row['game_nick'])}\n"
                    f"🆔 <b>Telegram ID:</b> <code>{row['user_id']}</code>\n"
                    f"✈️ <b>Username:</b> @{esc(row['username']) if row['username'] else 'не указан'}\n"
                    f"📅 <b>Добавлен:</b> {format_time(row['added_at'])}",
                    reply_markup=partner_keyboard(row["user_id"]),
                )

        elif action == "stats":
            stats = con.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved,
                    SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected
                FROM applications
                """
            ).fetchone()

            partners = con.execute(
                "SELECT COUNT(*) AS n FROM partners"
            ).fetchone()["n"]

            await callback.message.answer(
                "📊 <b>СТАТИСТИКА ALIANT MEDIA</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"📨 <b>Всего заявок:</b> {stats['total'] or 0}\n"
                f"⏳ <b>На рассмотрении:</b> {stats['pending'] or 0}\n"
                f"✅ <b>Одобрено:</b> {stats['approved'] or 0}\n"
                f"❌ <b>Отклонено:</b> {stats['rejected'] or 0}\n"
                f"🤝 <b>Активных медиа:</b> {partners}"
            )

        elif action == "logs":
            rows = con.execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT 20"
            ).fetchall()

            if not rows:
                await callback.message.answer("📒 <b>ЖУРНАЛ ПОКА ПУСТ.</b>")
            else:
                # Компактная таблица: номер, дата, автор, действие, детали.
                lines = [
                    "📒 <b>ЖУРНАЛ ДЕЙСТВИЙ</b>",
                    "━━━━━━━━━━━━━━━━━━",
                    "<pre>",
                    f"{'№':&lt;4} {'ДАТА':&lt;17} {'АДМИН':&lt;12}",
                    "─" * 34,
                    "</pre>",
                ]

                for row in rows:
                    lines.append(
                        f"\n<b>#{row['id']} · {format_time(row['created_at'])}</b>\n"
                        f"👤 <b>Кто:</b> <code>{row['actor_id']}</code>\n"
                        f"⚙️ <b>Действие:</b> {esc(row['action'])}\n"
                        f"📝 <b>Детали:</b> {esc(row['details'])}"
                    )

                await callback.message.answer("\n".join(lines))

        elif action == "admins":
            if not is_owner(callback.from_user.id):
                await callback.answer("Только владелец", show_alert=True)
                return

            rows = con.execute(
                "SELECT * FROM admins ORDER BY added_at"
            ).fetchall()

            lines = [
                "🛡 <b>АДМИНИСТРАТОРЫ ALIANT MEDIA</b>",
                "━━━━━━━━━━━━━━━━━━",
            ]

            for row in rows:
                role = "Владелец" if row["user_id"] == OWNER_ID else "Администратор"
                lines.append(
                    f"\n👤 <b>{role}</b>\n"
                    f"🆔 <code>{row['user_id']}</code>\n"
                    f"📅 <b>Добавлен:</b> {format_time(row['added_at'])}"
                )

            lines.append(
                "\n➕ <code>/addadmin ID</code> — добавить\n"
                "➖ <code>/deladmin ID</code> — удалить"
            )

            await callback.message.answer("\n".join(lines))

    await callback.answer()


# =========================================================
# УДАЛЕНИЕ МЕДИА-ПАРТНЁРА
# =========================================================

@dp.callback_query(F.data.startswith("partner:delete:"))
async def delete_partner(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer(
            "Удалять медиа-партнёров может только владелец.",
            show_alert=True,
        )
        return

    user_id = int(callback.data.rsplit(":", 1)[1])

    with db() as con:
        partner = con.execute(
            "SELECT * FROM partners WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        if partner:
            con.execute(
                "DELETE FROM partners WHERE user_id = ?",
                (user_id,),
            )
            con.execute(
                """
                UPDATE applications SET status = 'removed'
                WHERE user_id = ? AND status = 'approved'
                """,
                (user_id,),
            )

    if not partner:
        await callback.answer("Медиа-партнёр не найден", show_alert=True)
        return

    audit(
        callback.from_user.id,
        "Медиа-партнёр удалён",
        f"{partner['game_nick']} ({user_id})",
    )

    try:
        await bot.send_message(
            user_id,
            "📩 <b>СТАТУС ALIANT MEDIA ИЗМЕНЁН</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Твой статус медиа-партнёра был снят администрацией.",
        )
    except Exception:
        logging.exception("Не удалось уведомить удалённого партнёра")

    await callback.message.edit_text(
        "🗑 <b>МЕДИА-ПАРТНЁР УДАЛЁН</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎮 <b>Ник:</b> {esc(partner['game_nick'])}\n"
        f"🆔 <b>Telegram ID:</b> <code>{user_id}</code>"
    )
    await callback.answer("Медиа-партнёр удалён")


# =========================================================
# УПРАВЛЕНИЕ АДМИНИСТРАТОРАМИ
# =========================================================

@dp.message(Command("addadmin"))
async def add_admin(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("⛔ <b>Команда доступна только владельцу.</b>")
        return

    parts = (message.text or "").split()

    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer(
            "Формат команды:\n<code>/addadmin 123456789</code>"
        )
        return

    user_id = int(parts[1])

    with db() as con:
        con.execute(
            """
            INSERT OR IGNORE INTO admins(user_id, added_by, added_at)
            VALUES (?, ?, ?)
            """,
            (user_id, message.from_user.id, int(time.time())),
        )

    audit(message.from_user.id, "Администратор добавлен", str(user_id))

    await message.answer(
        f"✅ <b>Администратор <code>{user_id}</code> добавлен.</b>"
    )


@dp.message(Command("deladmin"))
async def remove_admin(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("⛔ <b>Команда доступна только владельцу.</b>")
        return

    parts = (message.text or "").split()

    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer(
            "Формат команды:\n<code>/deladmin 123456789</code>"
        )
        return

    user_id = int(parts[1])

    if user_id == OWNER_ID:
        await message.answer("⚠️ <b>Нельзя удалить владельца.</b>")
        return

    with db() as con:
        con.execute(
            "DELETE FROM admins WHERE user_id = ?",
            (user_id,),
        )

    audit(message.from_user.id, "Администратор удалён", str(user_id))

    await message.answer(
        f"🗑 <b>Администратор <code>{user_id}</code> удалён.</b>"
    )


# =========================================================
# НЕИЗВЕСТНЫЕ СООБЩЕНИЯ
# =========================================================

@dp.message()
async def fallback(message: Message, state: FSMContext):
    if await state.get_state():
        await message.answer(
            "⚠️ <b>Отправь ответ на текущий вопрос текстом.</b>\n"
            "Для отмены используй <code>/cancel</code>."
        )
        return

    await message.answer(
        "Выбери действие в меню:\n"
        "👤 <b>Профиль</b>\n"
        "📝 <b>Подать заявку</b>",
        reply_markup=user_keyboard(),
    )


# =========================================================
# ЗАПУСК
# =========================================================

async def main():
    init_db()
    logging.info("Aliant Media bot started")
    logging.info("Database: %s", DB_PATH.resolve())
    logging.info("Logo: %s", LOGO_PATH.resolve())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
