"""Offline operations: python -m study.manage --help."""
import argparse
from contextlib import closing
from datetime import datetime, UTC, timedelta
import os
from pathlib import Path
import sqlite3
import time

from sqlalchemy import select, func
from sqlalchemy.engine import make_url

from .config import Settings
from .db import Database
from .models import BotOutbox, BotMessage, Draft, Invite, Delivery, User


def sqlite_path(url):
    parsed = make_url(url)
    if parsed.drivername != 'sqlite' or not parsed.database or parsed.database == ':memory:':
        raise ValueError('Команда поддерживает только файловую SQLite')
    return Path(parsed.database).resolve()


def checked_connection(path):
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)
    try:
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Проверка целостности SQLite не пройдена')
        return conn
    except BaseException:
        conn.close()
        raise


def backup(url, destination):
    source = sqlite_path(url)
    destination = Path(destination).resolve()
    if source == destination:
        raise ValueError('Резервная копия должна быть отдельным файлом')
    # Exclusive creation avoids silently overwriting the only good backup.
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with closing(checked_connection(source)) as src, closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
        checked_connection(destination).close()
        with destination.open('rb') as handle:
            os.fsync(handle.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def restore(url, source):
    target = sqlite_path(url)
    source = Path(source).resolve()
    if source == target:
        raise ValueError('Источник восстановления совпадает с рабочей базой')
    with closing(checked_connection(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    os.chmod(target, 0o600)
    checked_connection(target).close()


def bot_health(db, heartbeat=Path('/tmp/study-bot-heartbeat'), polling=Path('/tmp/study-bot-polling')):
    for path in (heartbeat, polling):
        if not path.exists() or time.time()-path.stat().st_mtime > 120:
            raise RuntimeError('Нет свежего heartbeat scheduler или Telegram polling')
    with db.session() as s:
        dead = s.scalar(select(func.count()).select_from(BotOutbox).where(BotOutbox.failed.is_(True)))
        oldest = s.scalar(select(func.min(BotOutbox.retry_at)).where(BotOutbox.failed.is_(False)))
        if dead or (oldest and oldest < datetime.now(UTC).replace(tzinfo=None)-timedelta(minutes=15)):
            raise RuntimeError('Очередь ответов требует внимания оператора')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('migrate', help='Инициализировать/обновить схему; сначала остановите web и bot и сделайте backup')
    sub.add_parser('check', help='Проверить конфигурацию, версию схемы и целостность SQLite')
    sub.add_parser('bot-health', help='Проверить scheduler, базу и очередь ответов')
    sub.add_parser('requeue', help='Повторить доставку ответов после устранения причины сбоя')
    command = sub.add_parser('discard-failed', help='Удалить только failed-ответы; учебные результаты сохраняются')
    command.add_argument('--confirm', action='store_true', required=True)
    sub.add_parser('prune', help='Удалить истёкшие приглашения, старые ключи доставок и ненужные ссылки сообщений')
    command = sub.add_parser('backup', help='Создать проверенную резервную копию, не перезаписывая файл')
    command.add_argument('path')
    command = sub.add_parser('restore', help='Восстановить копию; только при остановленных сервисах')
    command.add_argument('path')
    command.add_argument('--confirm-stopped', action='store_true', required=True)
    command = sub.add_parser('set-teacher', help='Выдать или отозвать роль учителя по Telegram ID')
    command.add_argument('telegram_id', type=int)
    command.add_argument('--revoke', action='store_true')
    args = parser.parse_args()
    settings = Settings()
    if args.command == 'backup':
        backup(settings.database_url, args.path)
    elif args.command == 'restore':
        restore(settings.database_url, args.path)
    elif args.command == 'migrate':
        Database(settings.database_url).engine.dispose()
    else:
        db = Database(settings.database_url, initialize=False)
        if args.command == 'set-teacher':
            with db.session.begin() as s:
                user = s.scalar(select(User).where(User.telegram_id == args.telegram_id))
                if user is None:
                    parser.error('Пользователь с таким Telegram ID не найден')
                user.is_teacher = not args.revoke
        elif args.command == 'check':
            checked_connection(sqlite_path(settings.database_url)).close()
        elif args.command == 'bot-health':
            bot_health(db)
        elif args.command == 'requeue':
            with db.session.begin() as s:
                for row in s.scalars(select(BotOutbox).where(BotOutbox.failed.is_(True))):
                    row.failed, row.attempts, row.retry_at = False, 0, datetime.now(UTC).replace(tzinfo=None)
        elif args.command == 'discard-failed':
            with db.session.begin() as s:
                for row in s.scalars(select(BotOutbox).where(BotOutbox.failed.is_(True))):
                    s.delete(row)
        elif args.command == 'prune':
            now = datetime.now(UTC).replace(tzinfo=None)
            with db.session.begin() as s:
                for row in s.scalars(select(Invite).where(Invite.expires < now)): s.delete(row)
                for row in s.scalars(select(Delivery).where(Delivery.sent_at < now-timedelta(days=90))): s.delete(row)
                used = {d.data.get('message_id') for d in s.scalars(select(Draft))}
                used |= {r.payload.get('message_id') for r in s.scalars(select(BotOutbox))}
                for row in s.scalars(select(BotMessage)):
                    if row.placeholder not in used: s.delete(row)
    print('OK')


if __name__ == '__main__':
    main()
