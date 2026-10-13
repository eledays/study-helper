import asyncio
import logging
from datetime import datetime, date, timedelta, UTC
from uuid import uuid4
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
from aiogram.exceptions import TelegramForbiddenError, TelegramAPIError
from sqlalchemy import select
from .config import Settings, setup_logging
from .db import Database
from .models import User, Subject, Task, Draft, Daily, Invite, Slot, Delivery, Activity, Score
from .services import record_activity, weekly_text, progress

log = logging.getLogger(__name__)


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=value) for label, value in row] for row in rows])


def local_day(user, now=None):
    return (now or datetime.now(ZoneInfo(user.timezone))).astimezone(ZoneInfo(user.timezone)).date()


def menu(user, day=None):
    day = day or local_day(user)
    rest = {'female': 'Сегодня отдыхала', 'male': 'Сегодня отдыхал'}.get(user.gender, 'Сегодня день отдыха')
    return keyboard([[('Записать занятие', 'begin:practice'), ('Внести пробник', 'begin:exam')],
                     [(rest, f'daily:{day}:rest'), ('Не получилось', f'daily:{day}:missed')],
                     [('Цели недели', 'goals'), ('Итоги недели', 'weekly')], [('История и исправления', 'history')]])


def daily_keyboard(s, user, day):
    rows = []
    for slot in s.scalars(select(Slot).where(Slot.student_id == user.id, Slot.day == day).order_by(Slot.start)):
        if slot.kind != 'rest' and slot.subject_id:
            rows.append([(slot.title[:55], f'slot:{slot.id}')])
    return InlineKeyboardMarkup(
        inline_keyboard=keyboard(rows).inline_keyboard + menu(user,day).inline_keyboard)


def get_user(s, telegram):
    user = s.scalar(select(User).where(User.telegram_id == telegram.id))
    if not user:
        user = User(telegram_id=telegram.id, name=telegram.first_name or '')
        s.add(user); s.flush()
    return user


def draft_set(s, user_id, data):
    row = s.get(Draft, user_id)
    if row:
        row.data = data
    else:
        s.add(Draft(student_id=user_id, data=data))


def mood_keyboard(kind, day):
    labels = ['Очень тяжело', 'Тяжело', 'Нормально', 'Хорошо', 'Очень хорошо'] if kind == 'mood' else ['Легко', 'Небольшая', 'Средняя', 'Высокая', 'Очень высокая']
    return keyboard([[(f'{n} · {label}', f'feel:{day}:{kind}:{n}')] for n, label in enumerate(labels, 1)] + [[('Пропустить', f'feel:{day}:{kind}:skip')]])


def router(db, settings):
    dp = Dispatcher()
    async def private_message(message):
        return message.chat.type == 'private'
    async def private_callback(call):
        return call.message is not None and call.message.chat.type == 'private'
    dp.message.filter(private_message)
    dp.callback_query.filter(private_callback)

    async def ask_wellbeing(message,s,user,day):
        if not user.wellbeing: return
        daily = s.scalar(select(Daily).where(Daily.student_id == user.id, Daily.day == day))
        if not daily:
            daily=Daily(student_id=user.id,day=day);s.add(daily)
        if daily.wellbeing_asked: return
        daily.wellbeing_asked=True
        await message.answer('Как самочувствие? Можно пропустить.',reply_markup=mood_keyboard('mood',day))

    async def send_menu(message, user, text='Что запишем сегодня?'):
        await message.answer(text, reply_markup=menu(user))

    @dp.message(CommandStart())
    async def start(message: Message):
        with db.session.begin() as s:
            user = get_user(s, message.from_user)
            user.bot_active = True
            arg = message.text.partition(' ')[2]
            if arg.startswith('invite_'):
                token = arg.removeprefix('invite_')
                invite = s.get(Invite, token)
                if not invite or invite.expires < datetime.now(UTC).replace(tzinfo=None) or invite.teacher_id == user.id:
                    await message.answer('Приглашение недействительно. Попросите учителя создать новое.'); return
                teacher = s.get(User, invite.teacher_id)
                await message.answer(f'Учитель {teacher.name} сможет менять план и видеть учебные результаты. Подключить?',
                    reply_markup=keyboard([[('Подключить', f'accept:{token}'), ('Отмена', 'home')]])); return
            await send_menu(message, user, 'Привет! Здесь можно спокойно записывать занятия и результаты. Начнём с того, что получилось сегодня?')
            await message.answer(f'План, предметы и настройки: {settings.base_url}')

    @dp.message(Command('cancel'))
    async def cancel(message):
        with db.session.begin() as s:
            user = get_user(s, message.from_user)
            draft = s.get(Draft, user.id)
            if draft: s.delete(draft)
            await send_menu(message, user, 'Ввод отменён. Можно начать заново.')

    async def subject_menu(message, s, user, kind, day=None):
        subjects = list(s.scalars(select(Subject).where(Subject.student_id == user.id)))
        if not subjects:
            await message.answer(f'Сначала добавьте предмет и задания на сайте: {settings.base_url}/settings'); return
        draft_set(s, user.id, {'stage': 'subject', 'kind': kind, 'day': (day or local_day(user)).isoformat(), 'key': uuid4().hex})
        await message.answer('Выберите предмет.', reply_markup=keyboard([[(sub.name[:60], f'sub:{sub.id}')] for sub in subjects]))

    async def type_menu(message, data):
        await message.answer('Что получилось сделать?', reply_markup=keyboard([
            [('Тестовая часть', 'type:test')], [('Выбрать задания', 'type:practice')], [('Другая дата', 'date')], [('Отменить', 'cancel')]]))

    async def tasks_menu(message, tasks, data):
        selected = data.get('selected', [])
        rows = [[(('✓ ' if t.id in selected else '') + t.name[:50], f'toggle:{t.id}')] for t in tasks]
        rows += [[('Продолжить', 'score:start')], [('Отменить', 'cancel')]]
        await message.answer('Выберите решённые задания. Можно несколько.', reply_markup=keyboard(rows))

    async def score_next(message, s, data):
        index = data.get('index', 0)
        selected = data['selected']
        if index >= len(selected):
            data['stage'] = 'confirm'
            await message.answer('Все баллы внесены. Сохранить результат?', reply_markup=keyboard([[('Сохранить', f"save:{data['key'][:8]}"), ('Отменить', 'cancel')]])); return
        task = s.get(Task, selected[index])
        maximum = data.get('maxima', {}).get(str(task.id), task.max_score)
        data['stage'] = 'score'
        buttons = [(str(n), f"points:{data['key'][:8]}:{index}:{n}") for n in range(maximum+1)]
        rows = [buttons[n:n+5] for n in range(0, len(buttons), 5)]
        if data['kind'] == 'exam': rows.append([('Не решалось', f"points:{data['key'][:8]}:{index}:skip")])
        await message.answer(f'{task.name}: сколько баллов из {maximum}?', reply_markup=keyboard(rows))

    @dp.callback_query()
    async def callback(call: CallbackQuery):
        await call.answer()
        if not call.message: return
        with db.session.begin() as s:
            user = get_user(s, call.from_user)
            command, _, arg = (call.data or '').partition(':')
            row = s.get(Draft, user.id)
            data = dict(row.data) if row else {}
            try:
                if command in ('home', 'cancel'):
                    if row: s.delete(row)
                    await send_menu(call.message, user); return
                if command == 'accept':
                    invite = s.get(Invite, arg)
                    if not invite or invite.expires < datetime.now(UTC).replace(tzinfo=None) or invite.teacher_id == user.id:
                        raise ValueError('Приглашение недействительно')
                    if user.teacher_id and user.teacher_id != invite.teacher_id:
                        raise ValueError('Сначала отключите текущего учителя в настройках')
                    user.teacher_id = invite.teacher_id
                    s.delete(invite)
                    await call.message.answer('Учитель подключён. Доступ к самочувствию настраивается отдельно на сайте.'); return
                if command == 'history':
                    activities = list(s.scalars(select(Activity).where(Activity.student_id == user.id).order_by(Activity.day.desc(), Activity.id.desc()).limit(20)))
                    rows = [[(f'{a.day.strftime("%d.%m")} · {s.get(Subject,a.subject_id).name} · {"пробник" if a.kind == "exam" else "занятие"}', f'edit:{a.id}')] for a in activities]
                    await call.message.answer('Последние записи. Выберите запись для исправления.' if rows else 'Записей пока нет.', reply_markup=keyboard(rows)); return
                if command == 'edit':
                    activity = s.get(Activity, int(arg))
                    if not activity or activity.student_id != user.id: raise ValueError('Запись не найдена')
                    scores = list(s.scalars(select(Score).where(Score.activity_id == activity.id)))
                    lines = [f'{s.get(Task,sc.task_id).name}: {sc.score}/{sc.maximum}' + ('' if sc.attempted else ' · не решалось') for sc in scores]
                    await call.message.answer(f'Результат за {activity.day.strftime("%d.%m.%Y")}\n'+'\n'.join(lines), reply_markup=keyboard([[('Исправить баллы','revise:'+str(activity.id)),('Удалить','remove:'+str(activity.id))]])); return
                if command == 'revise':
                    activity = s.get(Activity, int(arg))
                    if not activity or activity.student_id != user.id: raise ValueError('Запись не найдена')
                    scores = list(s.scalars(select(Score).where(Score.activity_id == activity.id).order_by(Score.id)))
                    data = {'kind':activity.kind,'subject':activity.subject_id,'day':activity.day.isoformat(),'title':activity.title,
                            'key':uuid4().hex,'selected':[sc.task_id for sc in scores], 'index':0,'values':{}, 'replace':activity.id,
                            'maxima':{str(sc.task_id):sc.maximum for sc in scores}}
                    await score_next(call.message,s,data); draft_set(s,user.id,data); return
                if command == 'remove':
                    activity = s.get(Activity, int(arg))
                    if not activity or activity.student_id != user.id: raise ValueError('Запись не найдена')
                    await call.message.answer('Удалить эту запись?',reply_markup=keyboard([[('Да, удалить','delete:'+str(activity.id)),('Отмена','home')]])); return
                if command == 'delete':
                    activity = s.get(Activity, int(arg))
                    if not activity or activity.student_id != user.id: raise ValueError('Запись не найдена')
                    for score in s.scalars(select(Score).where(Score.activity_id == activity.id)): s.delete(score)
                    s.delete(activity)
                    await call.message.answer('Запись удалена.',reply_markup=menu(user)); return
                if command == 'begin':
                    if arg not in ('practice', 'exam'): return
                    await subject_menu(call.message, s, user, arg); return
                if command == 'slot':
                    slot = s.get(Slot, int(arg))
                    if not slot or slot.student_id != user.id or not slot.subject_id:
                        raise ValueError('Занятие не найдено')
                    data = {'stage': 'type', 'kind': 'exam' if slot.kind == 'exam' else 'practice',
                            'subject': slot.subject_id, 'day': slot.day.isoformat(), 'key': uuid4().hex, 'title': slot.title}
                    if slot.kind == 'exam':
                        data['selected'] = [t.id for t in s.scalars(select(Task).where(Task.subject_id == slot.subject_id).order_by(Task.id))]
                        if not data['selected']: raise ValueError('Сначала добавьте задания на сайте')
                        data.update(index=0, values={})
                        await score_next(call.message, s, data)
                    elif slot.task_ids:
                        data.update(stage='select',selected=list(slot.task_ids),part='all')
                        tasks=list(s.scalars(select(Task).where(Task.subject_id==slot.subject_id).order_by(Task.id)))
                        await tasks_menu(call.message,tasks,data)
                    else:
                        await type_menu(call.message, data)
                    draft_set(s, user.id, data); return
                if command in ('goals', 'weekly'):
                    text = weekly_text(s, user.id, local_day(user)) if command == 'weekly' else '\n'.join(
                        f"{r['label']}: {r['done']} из {r['target']}" for r in progress(s, user.id, local_day(user))) or 'Целей пока нет. Их можно добавить на сайте.'
                    await call.message.answer(text); return
                if command == 'daily':
                    day_text, _, arg = arg.partition(':')
                    day = date.fromisoformat(day_text)
                    if day > local_day(user) or arg not in ('rest', 'missed'): return
                    daily = s.scalar(select(Daily).where(Daily.student_id == user.id, Daily.day == day))
                    if not daily:
                        daily = Daily(student_id=user.id, day=day)
                        s.add(daily)
                    daily.status = arg
                    await call.message.answer('День отдыха отмечен.' if arg == 'rest' else 'Отметка сохранена.')
                    await ask_wellbeing(call.message,s,user,day)
                    return
                if command == 'feel':
                    if not user.wellbeing: return
                    day_text, kind, value = arg.split(':',2)
                    day = date.fromisoformat(day_text)
                    if day > local_day(user): return
                    if kind not in ('mood', 'load'): return
                    if value != 'skip' and value not in ('1','2','3','4','5'): return
                    daily = s.scalar(select(Daily).where(Daily.student_id == user.id, Daily.day == day))
                    if not daily: daily = Daily(student_id=user.id, day=day); s.add(daily)
                    if value != 'skip': setattr(daily, kind, int(value))
                    if kind == 'mood': await call.message.answer('Какой была нагрузка? Можно пропустить.', reply_markup=mood_keyboard('load',day))
                    else: await call.message.answer('Сохранено.' if value != 'skip' else 'Опрос завершён.')
                    return
                if not data:
                    await call.message.answer('Этот ввод уже завершён. Начните новую запись.', reply_markup=menu(user)); return
                if command == 'sub' and data.get('stage') == 'subject':
                    subject = s.get(Subject, int(arg))
                    if not subject or subject.student_id != user.id: raise ValueError('Предмет не найден')
                    data['subject'] = subject.id
                    if data['kind'] == 'exam':
                        data['selected'] = [t.id for t in s.scalars(select(Task).where(Task.subject_id == subject.id).order_by(Task.id))]
                        if not data['selected']: raise ValueError('Сначала добавьте задания на сайте')
                        data.update(index=0, values={})
                        data['stage'] = 'exam-ready'
                        await call.message.answer('Пробник за сегодня?', reply_markup=keyboard([[('Да, внести баллы','exam-start'),('Другая дата','date')]]))
                    else:
                        data['stage'] = 'type'; await type_menu(call.message, data)
                elif command == 'exam-start' and data.get('stage') == 'exam-ready':
                    await score_next(call.message, s, data)
                elif command == 'date' and data.get('stage') in ('type','exam-ready'):
                    data['stage'] = 'date'
                    await call.message.answer('Введите дату в формате ДД.ММ.ГГГГ.')
                elif command == 'type' and data.get('stage') == 'type':
                    tasks = list(s.scalars(select(Task).where(Task.subject_id == data['subject']).order_by(Task.id)))
                    if arg == 'test':
                        data['stage'] = 'test-choice'
                        await call.message.answer('Вся тестовая часть или отдельные задания?', reply_markup=keyboard([
                            [('Все задания', 'full'), ('Конкретные', 'partial')]]))
                    elif arg == 'practice':
                        data.update(stage='select', kind='practice', selected=[], part='all')
                        await tasks_menu(call.message, tasks, data)
                elif command in ('full', 'partial') and data.get('stage') == 'test-choice':
                    tasks = list(s.scalars(select(Task).where(Task.subject_id == data['subject'], Task.part == 'test').order_by(Task.id)))
                    if not tasks: raise ValueError('Добавьте задания тестовой части на сайте')
                    if command == 'full':
                        data.update(kind='test', selected=[t.id for t in tasks], index=0, values={})
                        await score_next(call.message, s, data)
                    else:
                        data.update(stage='select', kind='practice', selected=[], part='test')
                        await tasks_menu(call.message, tasks, data)
                elif command == 'toggle' and data.get('stage') == 'select':
                    tasks = list(s.scalars(select(Task).where(Task.subject_id == data['subject']).order_by(Task.id)))
                    if data.get('part') == 'test': tasks = [t for t in tasks if t.part == 'test']
                    if int(arg) not in [t.id for t in tasks]: raise ValueError('Задание не найдено')
                    selected = list(data['selected'])
                    if int(arg) in selected: selected.remove(int(arg))
                    else: selected.append(int(arg))
                    data['selected'] = selected
                    await call.message.edit_reply_markup(reply_markup=keyboard(
                        [[(('✓ ' if t.id in selected else '')+t.name[:50], f'toggle:{t.id}')] for t in tasks]+[[('Продолжить','score:start'),('Отменить','cancel')]]))
                elif command == 'score' and data.get('stage') == 'select':
                    if not data['selected']: raise ValueError('Выберите хотя бы одно задание')
                    data.update(index=0, values={})
                    await score_next(call.message, s, data)
                elif command == 'points' and data.get('stage') == 'score':
                    nonce, index_text, arg = arg.split(':', 2)
                    if nonce != data['key'][:8] or int(index_text) != data['index']:
                        await call.message.answer('Этот балл уже записан. Используйте кнопки текущего задания.'); return
                    task_id = data['selected'][data['index']]
                    task = s.get(Task, task_id)
                    value = None if arg == 'skip' and data['kind'] == 'exam' else int(arg)
                    if value is not None and not 0 <= value <= data.get('maxima', {}).get(str(task_id), task.max_score): raise ValueError('Балл вне диапазона')
                    values = dict(data['values']); values[str(task_id)] = value
                    data['values'] = values; data['index'] += 1
                    await score_next(call.message, s, data)
                elif command == 'save' and data.get('stage') == 'confirm' and arg == data['key'][:8]:
                    if data.get('replace'):
                        activity = s.get(Activity, data['replace'])
                        if not activity or activity.student_id != user.id: raise ValueError('Запись уже удалена')
                        # Keep historical maximum scores and the original grading scale on correction.
                        scores = list(s.scalars(select(Score).where(Score.activity_id == activity.id)))
                        for score in scores:
                            value = data['values'].get(str(score.task_id))
                            score.attempted = value is not None
                            score.score = value or 0
                        if activity.kind == 'exam':
                            activity.secondary = activity.scale_snapshot.get(str(sum(sc.score for sc in scores)))
                        s.delete(row)
                        await call.message.answer('Исправленный результат сохранён.', reply_markup=menu(user)); return
                    activity = record_activity(s, user.id, user.id, data['subject'], date.fromisoformat(data['day']),
                        data['kind'], data['values'], data['key'], data.get('title', ''))
                    s.delete(row)
                    text = 'Результат сохранён.'
                    if activity.kind == 'exam':
                        text += f" Первичный балл: {sum(v or 0 for v in data['values'].values())}."
                        text += f' Вторичный: {activity.secondary}.' if activity.secondary is not None else ' Для этого результата нет строки в шкале перевода.'
                    await call.message.answer(text, reply_markup=menu(user))
                    if user.wellbeing and data['day'] == local_day(user).isoformat():
                        await ask_wellbeing(call.message,s,user,date.fromisoformat(data['day']))
                    return
                else:
                    await call.message.answer('Эта кнопка относится к предыдущему шагу. Продолжите текущий ввод или /cancel.'); return
                draft_set(s, user.id, data)
            except (ValueError, KeyError, IndexError) as error:
                s.rollback()
                await call.message.answer(str(error) if isinstance(error, ValueError) else 'Начните ввод заново: /cancel')

    @dp.message()
    async def text(message):
        with db.session.begin() as s:
            user = get_user(s, message.from_user)
            row = s.get(Draft, user.id)
            if row and row.data.get('stage') == 'date':
                try:
                    day = datetime.strptime(message.text or '', '%d.%m.%Y').date()
                    if day > local_day(user): raise ValueError()
                except ValueError:
                    await message.answer('Нужна сегодняшняя или прошлая дата в формате ДД.ММ.ГГГГ.'); return
                data = dict(row.data); data.update(day=day.isoformat(), stage='type')
                if data['kind'] == 'exam': await score_next(message,s,data)
                else: await type_menu(message,data)
                row.data = data
            else:
                await send_menu(message, user)
    return dp


def due_messages(s, user, now):
    local = now.astimezone(ZoneInfo(user.timezone))
    day = local.date()
    evening = datetime.combine(day, datetime.strptime(user.evening, '%H:%M').time(), ZoneInfo(user.timezone))
    slots = list(s.scalars(select(Slot).where(Slot.student_id == user.id, Slot.day == day)))
    if local >= evening:
        rested = any(slot.kind == 'rest' for slot in slots)
        texts = ['Как прошёл день? Давай отметим, что удалось сделать.', 'Что получилось сегодня? Запишем занятия.', 'Как сегодня шла подготовка? Можно отметить и день отдыха.']
        text = 'Сегодня был день отдыха. Так и получилось?' if rested else texts[day.toordinal() % len(texts)]
        yield f'{user.id}:daily:{day}', text, daily_keyboard(s, user, day)
        if day.weekday() == 6:
            yield f'{user.id}:weekly:{day}', weekly_text(s, user.id, day), None
    if user.reminders:
        for slot in slots:
            start = datetime.combine(day, datetime.strptime(slot.start, '%H:%M').time(), ZoneInfo(user.timezone))
            if slot.kind != 'rest' and start-timedelta(minutes=15) <= local < start:
                yield f'{user.id}:slot:{slot.id}', f'Через несколько минут по плану: {slot.title}.', None


async def deliver_due(bot, db, now):
    # Exactly one bot process. Persist each successful delivery independently.
    with db.session() as s:
        users = list(s.scalars(select(User).where(User.bot_active.is_(True))))
        pending = [(user, list(due_messages(s,user,now))) for user in users]
    for user, messages in pending:
        for key, text, markup in messages:
            with db.session() as s:
                if s.get(Delivery,key): continue
            try:
                await bot.send_message(user.telegram_id,text,reply_markup=markup)
            except TelegramForbiddenError:
                with db.session.begin() as s: s.get(User,user.id).bot_active=False
                break
            except (TelegramAPIError,OSError):
                log.warning('Notification failed user=%s',user.id)
                continue
            # A crash after Telegram accepted a message but before this commit can repeat it.
            with db.session.begin() as s: s.add(Delivery(key=key))


async def scheduler(bot, db):
    while True:
        try:
            await deliver_due(bot,db,datetime.now(ZoneInfo('UTC')))
        except Exception:
            log.exception('Scheduler cycle failed')
        await asyncio.sleep(30)


async def main():
    setup_logging()
    settings = Settings()
    if not settings.bot_token: raise RuntimeError('Задайте BOT_TOKEN')
    db = Database(settings.database_url)
    bot = Bot(settings.bot_token)
    task = asyncio.create_task(scheduler(bot, db))
    try:
        await router(db, settings).start_polling(bot, tasks_concurrency_limit=1)
    finally:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        await bot.session.close()

if __name__ == '__main__': asyncio.run(main())
