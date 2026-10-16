import asyncio
import logging
import fcntl
from pathlib import Path
from datetime import datetime, date, timedelta, UTC
from uuid import uuid4
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery, BotCommand, MessageEntity
from aiogram.exceptions import TelegramForbiddenError, TelegramAPIError, TelegramBadRequest
from aiogram.methods import GetUpdates
from sqlalchemy import select
from .presentation import display_label, task_order_key
from .messages import bot_html, bot_text, bot_texts, UserMessageError
from .result_input import full_result, grid_rows, parse_points
from .groups import ensure_groups, group_tasks
from .catalog import ensure_defaults
from .config import Settings, setup_logging
from .db import Database
from .responses import TransactionalResponses, response_session, drain_outbox
from .telegram_text import split_text
from .models import User, Subject, Task, Draft, Daily, Invite, Slot, Delivery, Activity, Score, TaskGroup, ExamTotal
from .services import record_activity, record_exam_total, weekly_text, progress, goal_cards

log = logging.getLogger(__name__)


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=value) for label, value in row] for row in rows])


def local_day(user, now=None):
    return (now or datetime.now(ZoneInfo(user.timezone))).astimezone(ZoneInfo(user.timezone)).date()


def get_user(s, telegram):
    user = s.scalar(select(User).where(User.telegram_id == telegram.id))
    if not user:
        user = User(telegram_id=telegram.id, name=telegram.first_name or '')
        s.add(user); s.flush()
        ensure_defaults(s, user.id)
    return user


def draft_set(s, user_id, data):
    row = s.get(Draft, user_id)
    if row:
        row.data = data
    else:
        s.add(Draft(student_id=user_id, data=data))


def mood_options():
    labels = bot_texts('wellbeing.practice_moods')
    ids = bot_texts('wellbeing.practice_mood_ids')
    if len(labels) != 5 or len(ids) != 5:
        raise ValueError('Wellbeing requires five emojis and five custom emoji IDs')
    return list(zip(labels, ids))


def mood_buttons(prefix):
    return [InlineKeyboardButton(text='\u200b' if emoji_id else label,
                icon_custom_emoji_id=emoji_id or None, callback_data=f'{prefix}:{value}')
            for value, (label, emoji_id) in enumerate(mood_options(), 1)]


def mood_confirmation(value):
    label, emoji_id = mood_options()[value - 1]
    marker = '{selected_mood}'
    template = bot_text('results.practice_mood_saved', mood=marker)
    before, after = template.split(marker, 1)
    entities = [MessageEntity(type='custom_emoji', offset=len(before.encode('utf-16-le')) // 2,
                length=len(label.encode('utf-16-le')) // 2, custom_emoji_id=emoji_id)] if emoji_id else None
    return before + label + after, entities


def mood_keyboard(kind, day):
    if kind == 'mood':
        return InlineKeyboardMarkup(inline_keyboard=[mood_buttons(f'feel:{day}:mood'),
            [InlineKeyboardButton(text=bot_text('buttons.skip'), callback_data=f'feel:{day}:mood:skip')]])
    labels = bot_texts('wellbeing.mood_labels' if kind == 'mood' else 'wellbeing.load_labels')
    return keyboard([[(bot_text('format.rating_button', number=n, label=label), f'feel:{day}:{kind}:{n}')] for n, label in enumerate(labels, 1)] + [[(bot_text('buttons.skip'), f'feel:{day}:{kind}:skip')]])


def router(db, settings):
    dp = Dispatcher()
    dp.message.middleware(TransactionalResponses(db))
    dp.callback_query.middleware(TransactionalResponses(db))
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
        await message.answer(bot_html('wellbeing.mood_question'),reply_markup=mood_keyboard('mood',day), parse_mode='HTML')

    async def render_step(message, data, text, reply_markup=None, entities=None):
        if data.get('message_id'):
            try:
                await message.bot.edit_message_text(text, chat_id=data['chat_id'], message_id=data['message_id'], reply_markup=reply_markup, entities=entities)
            except TelegramBadRequest as error:
                if 'message is not modified' not in error.message.lower():
                    raise
        else:
            sent = await message.answer(text, reply_markup=reply_markup, entities=entities)
            data.update(message_id=sent.message_id, chat_id=message.chat.id)

    def practice_tasks(s, data):
        tasks = group_tasks(s, data['group']) if data.get('group') else list(s.scalars(select(Task).where(Task.subject_id == data['subject'])))
        ids = set(data['all_ids'])
        return sorted((task for task in tasks if task.id in ids), key=task_order_key)

    async def write_access_message(message):
        return message.write_access_allowed is not None

    @dp.message(write_access_message)
    async def registration_welcome(message: Message):
        with response_session() as s:
            user = get_user(s, message.from_user)
            user.bot_active = True
            await message.answer(bot_text('welcome.greeting', base_url=settings.base_url.rstrip('/')))

    @dp.message(CommandStart())
    async def start(message: Message):
        with response_session() as s:
            user = get_user(s, message.from_user)
            user.bot_active = True
            arg = message.text.partition(' ')[2]
            if arg.startswith('invite_'):
                token = arg.removeprefix('invite_')
                invite = s.get(Invite, token)
                if not invite or invite.expires < datetime.now(UTC).replace(tzinfo=None) or invite.teacher_id == user.id or not s.get(User, invite.teacher_id).is_teacher:
                    await message.answer(bot_text('welcome.invalid_invite')); return
                teacher = s.get(User, invite.teacher_id)
                await message.answer(bot_text('welcome.teacher_invite', teacher_name=teacher.name),
                    reply_markup=keyboard([[(bot_text('buttons.connect_teacher'), f'accept:{token}'), (bot_text('buttons.cancel_entry'), 'home')]])); return
            await message.answer(bot_text('welcome.greeting', base_url=settings.base_url.rstrip('/')))

    @dp.message(Command('help'))
    async def help_command(message):
        await message.answer(bot_html('welcome.help', base_url=settings.base_url.rstrip('/')), parse_mode='HTML')

    async def show_goals(message, s, user):
        rows = progress(s, user.id, local_day(user))
        await message.answer(bot_html('goals.heading') + '\n\n' + goal_cards(rows), parse_mode='HTML')

    async def mark_day(message, s, user, day):
        daily = s.scalar(select(Daily).where(Daily.student_id == user.id, Daily.day == day))
        if not daily:
            daily = Daily(student_id=user.id, day=day)
            s.add(daily)
        daily.status = 'study' if s.scalar(select(Activity.id).where(Activity.student_id == user.id, Activity.day == day).limit(1)) else 'rest'
        await message.answer(bot_text('results.rest_marked'))
        key = f'{user.id}:daily:{day}'
        if not s.get(Delivery, key): s.add(Delivery(key=key))

    @dp.message(Command('practice', 'exam', 'goals', 'weekly', 'rest'))
    async def action_command(message, command: CommandObject):
        with response_session() as s:
            user = get_user(s, message.from_user)
            action = command.command
            if action in ('practice', 'exam'):
                await subject_menu(message, s, user, action)
            elif action == 'goals':
                await show_goals(message, s, user)
            elif action == 'weekly':
                await message.answer(weekly_text(s, user.id, local_day(user)), parse_mode='HTML')
            elif action == 'rest':
                await mark_day(message, s, user, local_day(user))

    @dp.message(Command('cancel'))
    async def cancel(message):
        with response_session() as s:
            user = get_user(s, message.from_user)
            draft = s.get(Draft, user.id)
            if draft: s.delete(draft)
            await message.answer(bot_text('entry.cancelled'))

    async def subject_menu(message, s, user, kind, day=None):
        subjects = list(s.scalars(select(Subject).where(Subject.student_id == user.id)))
        if not subjects:
            await message.answer(bot_text('entry.no_subjects', base_url=settings.base_url.rstrip('/'))); return
        data = {'stage': 'subject', 'kind': kind, 'day': (day or local_day(user)).isoformat(), 'key': uuid4().hex}
        await render_step(message, data, bot_text('entry.subject'), keyboard([[(display_label(sub.name)[:60], f'sub:{sub.id}')] for sub in subjects]))
        draft_set(s, user.id, data)

    async def type_menu(message, s, data):
        ensure_groups(s, data['subject'])
        groups = list(s.scalars(select(TaskGroup).where(TaskGroup.subject_id == data['subject']).order_by(TaskGroup.id)))
        rows = [[(group.name[:55], f"group:{data['key'][:8]}:{group.id}")] for group in groups if group_tasks(s, group.id)]
        rows += [[(bot_text('buttons.individual_tasks'), 'type:practice')], [(bot_text('buttons.other_date'), 'date'), (bot_text('buttons.cancel_entry'), 'cancel')]]
        await render_step(message, data, bot_text('entry.kind'), keyboard(rows))

    async def exam_menu(message, data):
        data['stage'] = 'exam-ready'
        await render_step(message, data, bot_text('entry.exam_mode'), keyboard([
            [(bot_text('buttons.exam_total'), f"exam-total:{data['key'][:8]}")],
            [(bot_text('buttons.exam_bulk'), f"exam-bulk:{data['key'][:8]}")],
            [(bot_text('buttons.exam_select'), f"exam-select:{data['key'][:8]}")],
            [(bot_text('buttons.other_date'), 'date'), (bot_text('buttons.cancel_entry'), 'cancel')]]))

    async def save_result(message, s, user, data):
        row = s.get(Draft, user.id)
        data['stage'] = 'saved'
        if 'total' in data:
            activity = record_exam_total(s, user.id, user.id, data['subject'], date.fromisoformat(data['day']), data['total'], data['key'], data.get('title', ''))
        else:
            activity = record_activity(s, user.id, user.id, data['subject'], date.fromisoformat(data['day']),
                data['kind'], data['values'], data['key'], data.get('title', ''))
        if row: s.delete(row)
        if data.get('practice_flow') or data['kind'] == 'exam':
            text = bot_text('results.practice_saved')
            if activity.kind == 'exam':
                text += bot_text('results.secondary_suffix', secondary=activity.secondary) if activity.secondary is not None else bot_text('results.missing_scale_suffix')
            markup = None
            day = date.fromisoformat(data['day'])
            daily = s.scalar(select(Daily).where(Daily.student_id == user.id, Daily.day == day))
            if user.wellbeing and day == local_day(user) and daily and not daily.wellbeing_asked:
                daily.wellbeing_asked = True
                text += '\n' + bot_text('wellbeing.practice_question')
                markup = InlineKeyboardMarkup(inline_keyboard=[mood_buttons(f'result-feel:{activity.id}')])
            await render_step(message, data, text, markup)
            return
        text = bot_text('results.saved')
        if activity.kind == 'exam':
            text += bot_text('results.primary_suffix', primary=data.get('total', sum(v or 0 for v in data.get('values', {}).values())))
            text += bot_text('results.secondary_suffix', secondary=activity.secondary) if activity.secondary is not None else bot_text('results.missing_scale_suffix')
        await message.answer(text)
        if user.wellbeing and data['day'] == local_day(user).isoformat():
            await ask_wellbeing(message,s,user,date.fromisoformat(data['day']))
        return


    async def exceptions_menu(message, s, data, edit=False, notice=None):
        tasks = practice_tasks(s, data)
        phase = data['stage']
        if phase == 'group-skipped':
            selected = [t.id for t in tasks if t.id not in data.get('skipped', [])]
            action = 'missing'
        else:
            tasks = [t for t in tasks if t.id not in data.get('skipped', [])]
            selected, action = data.get('errors', []), 'mistake'
        rows = grid_rows(tasks, selected, lambda task_id: f"{action}:{data['key'][:8]}:{task_id}")
        all_selected = bool(tasks) and len(selected) == len(tasks)
        selection = 'none' if all_selected else 'all'
        rows += [[(bot_text('buttons.deselect_all' if all_selected else 'buttons.select_all'),
                   f"selection:{data['key'][:8]}:{phase}:{selection}")],
                 [(bot_text('buttons.continue'), f"exceptions-next:{data['key'][:8]}:{phase}")],
                 [(bot_text('buttons.cancel_entry'), 'cancel')]]
        text = bot_text('entry.missing_tasks' if phase == 'group-skipped' else 'entry.mistakes')
        if notice: text += '\n' + notice
        await render_step(message, data, text, keyboard(rows))

    async def exam_selection(message, tasks, data, edit=False):
        rows = grid_rows(tasks, data.get('selected', []), lambda task_id: f"exam-toggle:{data['key'][:8]}:{task_id}")
        rows += [[(bot_text('buttons.continue'), f"exam-selected:{data['key'][:8]}")], [(bot_text('buttons.cancel_entry'), 'cancel')]]
        if edit:
            await message.edit_reply_markup(reply_markup=keyboard(rows))
        else:
            await message.answer(bot_text('entry.exam_selection'), reply_markup=keyboard(rows))

    async def tasks_menu(message, s, tasks, data):
        if not tasks: raise UserMessageError(bot_text('errors.add_tasks'))
        ids = set(data.get('selected', []))
        tasks = [task for task in tasks if not ids or task.id in ids]
        data.update(stage='group-skipped', practice_flow=True, all_ids=[t.id for t in tasks],
                    values=full_result(tasks), skipped=[], errors=[])
        await exceptions_menu(message, s, data)

    async def score_next(message, s, user, data):
        index = data.get('index', 0)
        selected = data['selected']
        if index >= len(selected):
            await save_result(message, s, user, data); return
        task = s.get(Task, selected[index])
        maximum = data.get('maxima', {}).get(str(task.id), task.max_score)
        data['stage'] = 'score'
        buttons = [(bot_text('format.score_button', score=n), f"points:{data['key'][:8]}:{index}:{n}") for n in range(maximum+1)]
        rows = [buttons[n:n+5] for n in range(0, len(buttons), 5)]
        if data['kind'] in ('exam', 'test'): rows.append([(bot_text('buttons.not_attempted'), f"points:{data['key'][:8]}:{index}:skip")])
        await render_step(message, data, bot_text('entry.task_score', task_name=task.name, maximum=maximum), keyboard(rows))

    @dp.callback_query()
    async def callback(call: CallbackQuery):
        await call.answer()
        if not call.message: return
        with response_session() as s:
            user = get_user(s, call.from_user)
            command, _, arg = (call.data or '').partition(':')
            row = s.get(Draft, user.id)
            if row and row.data.get('replace'):
                s.delete(row)
                row = None
            data = dict(row.data) if row else {}
            step_commands = {'cancel', 'sub', 'type', 'group', 'missing', 'mistake', 'selection', 'exceptions-next',
                             'toggle', 'score', 'points', 'date', 'exam-start', 'exam-total', 'exam-bulk',
                             'exam-select', 'exam-toggle', 'exam-selected', 'save'}
            if command in step_commands and not data:
                return
            if command in step_commands and data.get('message_id') and call.message.message_id != data['message_id']:
                return
            try:
                if command in ('home', 'cancel'):
                    if row: s.delete(row)
                    await call.message.answer(bot_text('entry.cancelled')); return
                if command == 'accept':
                    invite = s.get(Invite, arg)
                    if not invite or invite.expires < datetime.now(UTC).replace(tzinfo=None) or invite.teacher_id == user.id or not s.get(User, invite.teacher_id).is_teacher:
                        raise UserMessageError(bot_text('errors.invalid_invite'))
                    if user.teacher_id and user.teacher_id != invite.teacher_id:
                        raise UserMessageError(bot_text('errors.unlink_teacher'))
                    user.teacher_id = invite.teacher_id
                    s.delete(invite)
                    await call.message.answer(bot_text('welcome.teacher_connected')); return
                if command in ('history', 'edit', 'revise', 'remove', 'delete'):
                    await call.message.answer(bot_text('entry.history_site', base_url=settings.base_url.rstrip('/')))
                    return
                if command == 'begin':
                    if arg not in ('practice', 'exam'): return
                    await subject_menu(call.message, s, user, arg); return
                if command == 'slot':
                    slot = s.get(Slot, int(arg))
                    if not slot or slot.student_id != user.id or not slot.subject_id:
                        raise UserMessageError(bot_text('errors.slot_not_found'))
                    data = {'stage': 'type', 'kind': 'exam' if slot.kind == 'exam' else 'practice',
                            'subject': slot.subject_id, 'day': slot.day.isoformat(), 'key': uuid4().hex, 'title': slot.title}
                    if slot.kind == 'exam':
                        data['selected'] = [t.id for t in s.scalars(select(Task).where(Task.subject_id == slot.subject_id).order_by(Task.id))]
                        if not data['selected']: raise UserMessageError(bot_text('errors.add_tasks'))
                        data.update(index=0, values={})
                        await exam_menu(call.message, data)
                    elif slot.task_ids:
                        data.update(stage='select',selected=list(slot.task_ids),part='all')
                        tasks=list(s.scalars(select(Task).where(Task.subject_id==slot.subject_id).order_by(Task.id)))
                        await tasks_menu(call.message,s,tasks,data)
                    else:
                        await type_menu(call.message, s, data)
                    if data.get('stage') != 'saved': draft_set(s, user.id, data); return
                if command in ('goals', 'weekly'):
                    if command == 'weekly': await call.message.answer(weekly_text(s, user.id, local_day(user)), parse_mode='HTML')
                    else: await show_goals(call.message, s, user)
                    return
                if command == 'daily':
                    day_text, _, arg = arg.partition(':')
                    day = date.fromisoformat(day_text)
                    if day > local_day(user) or arg != 'rest': return
                    await mark_day(call.message, s, user, day)
                    return
                if command == 'result-feel':
                    if not user.wellbeing: return
                    activity_id, value = map(int, arg.split(':'))
                    if value not in range(1, 6): return
                    activity = s.get(Activity, activity_id)
                    if not activity or activity.student_id != user.id or activity.day != local_day(user): return
                    daily = s.scalar(select(Daily).where(Daily.student_id == user.id, Daily.day == activity.day))
                    if not daily or not daily.wellbeing_asked: return
                    daily.mood = value
                    text, entities = mood_confirmation(value)
                    await render_step(call.message, {'message_id': call.message.message_id, 'chat_id': call.message.chat.id},
                                      text, entities=entities)
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
                    if kind == 'mood': await call.message.answer(bot_html('wellbeing.load_question'), reply_markup=mood_keyboard('load',day), parse_mode='HTML')
                    else: await call.message.answer(bot_text('wellbeing.saved') if value != 'skip' else bot_text('wellbeing.finished'))
                    return
                if not data:
                    await call.message.answer(bot_text('errors.entry_finished')); return
                if command == 'sub' and data.get('stage') == 'subject':
                    subject = s.get(Subject, int(arg))
                    if not subject or subject.student_id != user.id: raise UserMessageError(bot_text('errors.subject_not_found'))
                    data['subject'] = subject.id
                    if data['kind'] == 'exam':
                        data['selected'] = [t.id for t in s.scalars(select(Task).where(Task.subject_id == subject.id).order_by(Task.id))]
                        if not data['selected']: raise UserMessageError(bot_text('errors.add_tasks'))
                        data.update(index=0, values={})
                        await exam_menu(call.message, data)
                    else:
                        data['stage'] = 'type'; await type_menu(call.message, s, data)
                elif command == 'exam-start' and data.get('stage') == 'exam-ready':
                    await score_next(call.message, s, user, data)
                elif command == 'date' and data.get('stage') in ('type','exam-ready'):
                    data['stage'] = 'date'
                    await render_step(call.message, data, bot_text('entry.date'))
                elif command == 'type' and data.get('stage') == 'type':
                    tasks = list(s.scalars(select(Task).where(Task.subject_id == data['subject']).order_by(Task.id)))
                    if arg == 'test':
                        ensure_groups(s, data['subject'])
                        group = s.scalar(select(TaskGroup).where(TaskGroup.subject_id == data['subject'], TaskGroup.kind == 'test'))
                        tasks = group_tasks(s, group.id)
                        if not tasks: raise UserMessageError(bot_text('errors.empty_group'))
                        data.update(stage='group-skipped', practice_flow=True, kind='test', group=group.id, all_ids=[t.id for t in tasks],
                                    values=full_result(tasks), skipped=[], errors=[])
                        await exceptions_menu(call.message, s, data)
                    elif arg == 'practice':
                        data.update(stage='select', kind='practice', selected=[], part='all')
                        await tasks_menu(call.message, s, tasks, data)
                elif command == 'group' and data.get('stage') == 'type':
                    nonce, group_id = arg.split(':')
                    if nonce != data['key'][:8]: return
                    group = s.get(TaskGroup, int(group_id))
                    if not group or group.subject_id != data['subject']: raise UserMessageError(bot_text('errors.group_not_found'))
                    tasks = group_tasks(s, group.id)
                    if not tasks: raise UserMessageError(bot_text('errors.empty_group'))
                    data.update(stage='group-skipped', practice_flow=True, kind='test' if group.kind == 'test' else 'practice', group=group.id,
                                title=group.name, all_ids=[t.id for t in tasks], values=full_result(tasks), skipped=[], errors=[])
                    await exceptions_menu(call.message, s, data)
                elif command == 'selection' and data.get('stage') in ('group-skipped', 'group-errors'):
                    nonce, phase, selection = arg.split(':')
                    if nonce != data['key'][:8] or phase != data['stage'] or selection not in ('all', 'none'): return
                    tasks = practice_tasks(s, data)
                    ids = [t.id for t in tasks]
                    if phase == 'group-skipped':
                        data['skipped'] = [] if selection == 'all' else ids
                    else:
                        ids = [task_id for task_id in ids if task_id not in data['skipped']]
                        data['errors'] = ids if selection == 'all' else []
                    await exceptions_menu(call.message, s, data)
                elif command in ('missing', 'mistake') and data.get('stage') == ('group-skipped' if command == 'missing' else 'group-errors'):
                    nonce, task_text = arg.split(':')
                    if nonce != data['key'][:8]: return
                    task_id = int(task_text)
                    if task_id not in data['all_ids'] or (command == 'mistake' and task_id in data['skipped']):
                        raise UserMessageError(bot_text('errors.task_not_found'))
                    field = 'skipped' if command == 'missing' else 'errors'
                    chosen = list(data[field])
                    if task_id in chosen: chosen.remove(task_id)
                    else: chosen.append(task_id)
                    data[field] = chosen
                    await exceptions_menu(call.message, s, data, edit=True)
                elif command == 'exceptions-next' and data.get('stage') in ('group-skipped', 'group-errors'):
                    nonce, phase = arg.split(':')
                    if nonce != data['key'][:8] or phase != data['stage']: return
                    values = dict(data['values'])
                    if data['stage'] == 'group-skipped':
                        if len(data['skipped']) == len(data['all_ids']):
                            await exceptions_menu(call.message, s, data, notice=bot_text('errors.select_task'))
                            return
                        for task_id in data['skipped']: values[str(task_id)] = None
                        data.update(stage='group-errors', values=values)
                        await exceptions_menu(call.message, s, data)
                    else:
                        selected = []
                        for task_id in data['errors']:
                            task = s.get(Task, task_id)
                            values[str(task_id)] = 0
                            if task.max_score > 1: selected.append(task_id)
                        # Omit unattempted tasks from ordinary practice; preserve them for a test part
                        if data['kind'] == 'practice': values = {k:v for k,v in values.items() if v is not None}
                        if not values or all(v is None for v in values.values()): raise UserMessageError(bot_text('errors.none_attempted'))
                        data.update(selected=[t.id for t in practice_tasks(s, data) if t.id in selected], index=0, values=values)
                        await score_next(call.message, s, user, data)
                elif command == 'exam-total' and data.get('stage') == 'exam-ready':
                    if arg != data['key'][:8]: return
                    maximum = sum(t.max_score for t in s.scalars(select(Task).where(Task.subject_id == data['subject'])))
                    data.update(stage='exam-total', total_maximum=maximum)
                    await render_step(call.message, data, bot_text('entry.exam_total', maximum=maximum))
                elif command in ('exam-bulk', 'exam-select') and data.get('stage') == 'exam-ready':
                    if arg != data['key'][:8]: return
                    tasks = list(s.scalars(select(Task).where(Task.subject_id == data['subject']).order_by(Task.id)))
                    if command == 'exam-bulk':
                        data['stage'] = 'exam-bulk'
                        order = ', '.join(bot_text('format.bulk_task', task_name=t.name, maximum=t.max_score) for t in tasks)
                        await render_step(call.message, data, bot_text('entry.exam_bulk', order=order))
                    else:
                        data['selected'] = []
                        await tasks_menu(call.message, s, tasks, data)
                elif command == 'exam-toggle' and data.get('stage') == 'exam-select':
                    nonce, task_text = arg.split(':')
                    if nonce != data['key'][:8]: return
                    tasks = list(s.scalars(select(Task).where(Task.subject_id == data['subject']).order_by(Task.id)))
                    task_id = int(task_text)
                    if task_id not in {t.id for t in tasks}: raise UserMessageError(bot_text('errors.task_not_found'))
                    chosen = list(data['selected'])
                    if task_id in chosen: chosen.remove(task_id)
                    else: chosen.append(task_id)
                    data['selected'] = chosen
                    await exam_selection(call.message, tasks, data, edit=True)
                elif command == 'exam-selected' and data.get('stage') == 'exam-select':
                    if arg != data['key'][:8]: return
                    tasks = list(s.scalars(select(Task).where(Task.subject_id == data['subject']).order_by(Task.id)))
                    chosen = set(data['selected'])
                    data.update(values={str(t.id): (1 if t.max_score == 1 else 0) if t.id in chosen else None for t in tasks},
                                selected=[t.id for t in tasks if t.id in chosen and t.max_score > 1], index=0)
                    await score_next(call.message, s, user, data)
                elif command == 'toggle' and data.get('stage') == 'select':
                    tasks = list(s.scalars(select(Task).where(Task.subject_id == data['subject']).order_by(Task.id)))
                    if data.get('part') == 'test': tasks = [t for t in tasks if t.part == 'test']
                    nonce, arg = arg.split(':')
                    if nonce != data['key'][:8]: return
                    if int(arg) not in [t.id for t in tasks]: raise UserMessageError(bot_text('errors.task_not_found'))
                    selected = list(data['selected'])
                    if int(arg) in selected: selected.remove(int(arg))
                    else: selected.append(int(arg))
                    data['selected'] = selected
                    await call.message.edit_reply_markup(reply_markup=keyboard(
                        grid_rows(tasks, selected, lambda task_id: f"toggle:{data['key'][:8]}:{task_id}")+[[(bot_text('buttons.continue'),'score:start'),(bot_text('buttons.cancel_entry'),'cancel')]]))
                elif command == 'score' and data.get('stage') == 'select':
                    if not data['selected']: raise UserMessageError(bot_text('errors.select_task'))
                    data.update(index=0, values={}, selected=sorted(data['selected']))
                    await score_next(call.message, s, user, data)
                elif command == 'points' and data.get('stage') == 'score':
                    nonce, index_text, arg = arg.split(':', 2)
                    if nonce != data['key'][:8] or int(index_text) != data['index']:
                        return
                    task_id = data['selected'][data['index']]
                    task = s.get(Task, task_id)
                    value = None if arg == 'skip' and data['kind'] in ('exam', 'test') else int(arg)
                    if value is not None and not 0 <= value <= data.get('maxima', {}).get(str(task_id), task.max_score): raise UserMessageError(bot_text('errors.score_range'))
                    values = dict(data['values']); values[str(task_id)] = value
                    data['values'] = values; data['index'] += 1
                    await score_next(call.message, s, user, data)
                elif command == 'save' and data.get('stage') == 'confirm' and arg == data['key'][:8]:
                    await save_result(call.message, s, user, data)
                    return
                else:
                    if command in step_commands: return
                    await call.message.answer(bot_text('errors.stale_button')); return
                if data.get('stage') != 'saved': draft_set(s, user.id, data)
            except (ValueError, KeyError, IndexError) as error:
                s.rollback()
                await call.message.answer(str(error) if isinstance(error, UserMessageError) else bot_text('errors.restart'))

    @dp.message()
    async def text(message):
        with response_session() as s:
            user = get_user(s, message.from_user)
            row = s.get(Draft, user.id)
            if row and row.data.get('replace'):
                s.delete(row)
                row = None
            if row and row.data.get('stage') == 'exam-total':
                data = dict(row.data)
                try:
                    primary = int(message.text or '')
                    if not 0 <= primary <= data['total_maximum']: raise UserMessageError()
                except ValueError:
                    await message.answer(bot_text('errors.integer_range', maximum=data['total_maximum'])); return
                data['total'] = primary
                await save_result(message, s, user, data)
            elif row and row.data.get('stage') == 'exam-bulk':
                data = dict(row.data)
                tasks = list(s.scalars(select(Task).where(Task.subject_id == data['subject']).order_by(Task.id)))
                try:
                    data['values'] = parse_points(message.text or '', tasks)
                except ValueError as error:
                    await message.answer(str(error)); return
                await save_result(message, s, user, data)
            elif row and row.data.get('stage') == 'date':
                try:
                    day = datetime.strptime(message.text or '', '%d.%m.%Y').date()
                    if day > local_day(user): raise UserMessageError()
                except ValueError:
                    await message.answer(bot_text('errors.past_date')); return
                data = dict(row.data); data.update(day=day.isoformat(), stage='type')
                if data['kind'] == 'exam': await exam_menu(message,data)
                else: await type_menu(message,s,data)
                row.data = data
            else:
                await message.answer(bot_text('entry.commands_hint'))
    return dp


def due_messages(s, user, now):
    local = now.astimezone(ZoneInfo(user.timezone))
    day = local.date()
    evening = datetime.combine(day, datetime.strptime(user.evening, '%H:%M').time(), ZoneInfo(user.timezone))
    slots = list(s.scalars(select(Slot).where(Slot.student_id == user.id, Slot.day == day)))
    if local >= evening:
        rested = any(slot.kind == 'rest' for slot in slots)
        texts = bot_texts('notifications.daily_variants')
        text = bot_text('notifications.rest_day') if rested else texts[day.toordinal() % len(texts)]
        if not s.get(Delivery, f'{user.id}:daily:{day}'):
            yield f'{user.id}:daily:{day}', text, None
        if day.weekday() == 6:
            yield f'{user.id}:weekly:{day}', weekly_text(s, user.id, day), None
    if user.reminders:
        for slot in slots:
            start = datetime.combine(day, datetime.strptime(slot.start, '%H:%M').time(), ZoneInfo(user.timezone))
            if slot.kind != 'rest' and start-timedelta(minutes=15) <= local < start:
                yield f'{user.id}:slot:{slot.id}', bot_text('notifications.slot', title=slot.title), None


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
                for index, chunk in enumerate(split_text(text, html=':weekly:' in key)):
                    part_key = f'{key}:part:{index}'
                    with db.session() as s:
                        if s.get(Delivery, part_key): continue
                    await bot.send_message(user.telegram_id,chunk,reply_markup=markup, parse_mode='HTML' if ':weekly:' in key else None)
                    with db.session.begin() as s: s.add(Delivery(key=part_key))
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
            await drain_outbox(bot, db)
            await deliver_due(bot,db,datetime.now(ZoneInfo('UTC')))
            Path('/tmp/study-bot-heartbeat').touch()
        except Exception:
            log.exception('Scheduler cycle failed')
        await asyncio.sleep(30)


async def main():
    setup_logging()
    settings = Settings()
    if not settings.bot_token: raise RuntimeError('Задайте BOT_TOKEN')
    from .manage import sqlite_path
    database_path = sqlite_path(settings.database_url)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    process_lock = (database_path.parent / '.bot.lock').open('a')
    try:
        fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Для этой базы уже запущен экземпляр бота') from None
    db = Database(settings.database_url, initialize=settings.environment != 'production')
    bot = Bot(settings.bot_token, session=AiohttpSession(proxy=settings.bot_proxy_url or None))
    async def polling_heartbeat(make_request, bot, method):
        response = await make_request(bot, method)
        if isinstance(method, GetUpdates):
            Path('/tmp/study-bot-polling').touch()
        return response
    bot.session.middleware(polling_heartbeat)
    await bot.set_my_commands([BotCommand(command=name, description=bot_text(f'commands.{name}'))
                               for name in ('start', 'practice', 'exam', 'goals', 'weekly', 'rest', 'help', 'cancel')])
    task = asyncio.create_task(scheduler(bot, db))
    try:
        await router(db, settings).start_polling(bot, tasks_concurrency_limit=1)
    finally:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        await bot.session.close()
        process_lock.close()

if __name__ == '__main__': asyncio.run(main())
