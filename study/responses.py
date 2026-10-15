"""Persist bot effects with business changes; perform Telegram I/O after commit."""
from contextlib import contextmanager
from contextvars import ContextVar
import asyncio
from datetime import datetime, UTC, timedelta
import logging
from types import SimpleNamespace
from uuid import uuid4

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage, EditMessageText, EditMessageReplyMarkup
from aiogram.types import CallbackQuery
from sqlalchemy import select

from .models import BotOutbox, BotMessage, Draft, User
from .telegram_text import split_text

log = logging.getLogger(__name__)
_session = ContextVar('bot_response_session')
METHODS = {'send_message': SendMessage, 'edit_message_text': EditMessageText,
           'edit_message_reply_markup': EditMessageReplyMarkup}


@contextmanager
def response_session():
    yield _session.get()


def enqueue(method, payload, placeholder=None):
    # Pydantic serializes keyboards/entities without persisting bot credentials.
    request = METHODS[method](**payload)
    _session.get().add(BotOutbox(chat_id=int(payload['chat_id']), method=method,
        payload=request.model_dump(mode='json', exclude_none=True, exclude_defaults=True),
        placeholder=placeholder))


class DeferredBot:
    def __init__(self, bot):
        self.original = bot

    async def edit_message_text(self, text, **kwargs):
        enqueue('edit_message_text', dict(text=text, **kwargs))


class DeferredMessage:
    def __init__(self, message):
        self.original = message
        self.bot = DeferredBot(message.bot)

    def __getattr__(self, name):
        return getattr(self.original, name)

    async def answer(self, text, **kwargs):
        chunks = split_text(text, html=kwargs.get('parse_mode') == 'HTML')
        for index, chunk in enumerate(chunks):
            placeholder = -int(uuid4().hex[:15], 16)
            options = dict(kwargs)
            if index < len(chunks)-1:
                options.pop('reply_markup', None)
            enqueue('send_message', dict(chat_id=self.chat.id, text=chunk, **options), placeholder)
        return SimpleNamespace(message_id=placeholder, chat=self.chat)

    async def edit_reply_markup(self, **kwargs):
        enqueue('edit_message_reply_markup', dict(chat_id=self.chat.id, message_id=self.message_id, **kwargs))


class DeferredCallback:
    def __init__(self, call):
        self.original = call
        self.message = DeferredMessage(call.message) if call.message else None

    def __getattr__(self, name):
        return getattr(self.original, name)

    async def answer(self):
        return None  # Already acknowledged before opening the transaction.


class TransactionalResponses(BaseMiddleware):
    def __init__(self, db):
        self.db = db

    async def __call__(self, handler, event, data):
        if isinstance(event, CallbackQuery):
            try:
                await event.answer()
            except (TelegramAPIError, OSError):
                log.warning('Callback acknowledgement failed')
            proxy = DeferredCallback(event)
        else:
            proxy = DeferredMessage(event)
        with self.db.session() as s:
            token = _session.set(s)
            try:
                result = await handler(proxy, data)
                s.commit()
            except BaseException:
                s.rollback()
                raise
            finally:
                _session.reset(token)
        # All awaited operations inside the handler only enqueue effects. No
        # SQLite transaction is held while waiting for the Telegram network.
        await drain_outbox(event.bot, self.db, chat_id=proxy.message.chat.id if isinstance(proxy, DeferredCallback) else proxy.chat.id)
        return result


async def drain_outbox(bot, db, chat_id=None, now=None):
    # The scheduler and interactive handlers share one bot process. Serialize
    # drains to prevent two tasks sending the same persisted effect at once.
    if not hasattr(db, '_outbox_locks'):
        db._outbox_locks = {}
    lock = db._outbox_locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())
    async with lock:
        await _drain_outbox(bot, db, chat_id, now)


async def _drain_outbox(bot, db, chat_id=None, now=None):
    now = now or datetime.now(UTC).replace(tzinfo=None)
    blocked = set()
    with db.session() as s:
        delayed_chats = select(BotOutbox.chat_id).where(BotOutbox.failed.is_(False), BotOutbox.retry_at > now)
        query = select(BotOutbox).where(BotOutbox.failed.is_(False),
            BotOutbox.chat_id.not_in(delayed_chats)).order_by(BotOutbox.id).limit(100)
        if chat_id is not None:
            query = query.where(BotOutbox.chat_id == chat_id)
        rows = list(s.scalars(query))
    for row in rows:
        if row.chat_id in blocked:
            continue
        if row.retry_at > now:
            blocked.add(row.chat_id)
            continue
        payload = dict(row.payload)
        reference = payload.get('message_id')
        if reference is not None and reference < 0:
            with db.session() as s:
                mapping = s.get(BotMessage, reference)
                source = s.scalar(select(BotOutbox).where(BotOutbox.placeholder == reference)) if mapping is None else None
            if mapping is None:
                if source is None or source.failed:
                    with db.session.begin() as s:
                        pending = s.get(BotOutbox, row.id)
                        if pending: pending.failed = True
                    log.error('Bot response has unavailable message reference outbox=%s', row.id)
                else:
                    blocked.add(row.chat_id)
                continue
            payload['message_id'] = mapping.message_id
        try:
            try:
                sent = await bot(METHODS[row.method](**payload))
            except TelegramBadRequest as error:
                if 'message is not modified' in error.message.lower():
                    sent = None
                elif row.method == 'edit_message_text' and any(fragment in error.message.lower() for fragment in
                        ('message to edit not found', "message can't be edited")):
                    sent = await bot.send_message(row.chat_id, payload['text'],
                        reply_markup=payload.get('reply_markup'), entities=payload.get('entities'))
                else:
                    raise
        except (TelegramAPIError, OSError) as error:
            blocked.add(row.chat_id)
            if isinstance(error, TelegramForbiddenError):
                # Blocking the bot is a normal user opt-out, not a global
                # service outage. No queued response to this chat can arrive.
                from sqlalchemy import delete
                with db.session.begin() as s:
                    user = s.scalar(select(User).where(User.telegram_id == row.chat_id))
                    if user: user.bot_active = False
                    s.execute(delete(BotOutbox).where(BotOutbox.chat_id == row.chat_id))
                log.info('Bot disabled for chat after Telegram refusal')
                continue
            with db.session.begin() as s:
                pending = s.get(BotOutbox, row.id)
                if pending:
                    pending.attempts += 1
                    pending.failed = pending.attempts >= 10
                    delay = error.retry_after if isinstance(error, TelegramRetryAfter) else min(900, 2 ** pending.attempts)
                    pending.retry_at = now + timedelta(seconds=delay)
            log.warning('Bot response delivery failed outbox=%s type=%s', row.id, type(error).__name__)
            continue
        with db.session.begin() as s:
            if row.placeholder and sent:
                s.merge(BotMessage(placeholder=row.placeholder, chat_id=row.chat_id, message_id=sent.message_id))
            if sent:
                for draft in s.scalars(select(Draft).where(Draft.student_id.in_(
                        select(User.id).where(User.telegram_id == row.chat_id)))):
                    saved = draft.data
                    if saved.get('chat_id') == row.chat_id and saved.get('message_id') in (row.placeholder, reference):
                        draft.data = dict(saved, message_id=sent.message_id)
            pending = s.get(BotOutbox, row.id)
            if pending:
                s.delete(pending)
