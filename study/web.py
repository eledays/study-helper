import hashlib
import hmac
import secrets
import time
from datetime import date, datetime, UTC, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import uuid4
from flask import Flask, request, session, g, redirect, url_for, render_template, abort, flash, jsonify
from sqlalchemy import select, delete
from .config import Settings, setup_logging
from .db import Database
from .models import User, Subject, Task, Goal, Slot, Activity, Score, Invite
from .services import accessible, subject_for, parse_scale, monday, progress, analytics, change_series


def verify_telegram(data, token, now=None):
    if not token or 'hash' not in data:
        return False
    try:
        age = (now or time.time()) - int(data['auth_date'])
        if not 0 <= age <= 600:
            return False
    except (KeyError, ValueError):
        return False
    check = '\n'.join(f'{k}={v}' for k, v in sorted(data.items()) if k != 'hash')
    key = hashlib.sha256(token.encode()).digest()
    return hmac.compare_digest(hmac.new(key, check.encode(), hashlib.sha256).hexdigest(), data['hash'])


def create_app(settings=None):
    settings = settings or Settings()
    if len(settings.secret_key) < 24:
        raise RuntimeError('Задайте SECRET_KEY длиной не менее 24 символов')
    setup_logging()
    app = Flask(__name__)
    app.config.update(SECRET_KEY=settings.secret_key, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=settings.cookie_secure,
                      MAX_CONTENT_LENGTH=128 * 1024, PERMANENT_SESSION_LIFETIME=timedelta(days=7))
    db = Database(settings.database_url)
    app.extensions['db'] = db

    @app.before_request
    def prepare():
        g.db = db.session()
        g.user = g.db.get(User, session.get('user_id')) if session.get('user_id') else None
        session.setdefault('csrf', secrets.token_urlsafe(24))
        if request.method == 'POST' and not hmac.compare_digest(request.form.get('csrf', ''), session['csrf']):
            abort(400, 'Обновите страницу и повторите действие')
        if request.endpoint not in ('login', 'auth', 'static', 'health') and not g.user:
            return redirect(url_for('login'))
        if g.user:
            student_id = request.values.get('student', str(g.user.id))
            try:
                g.student = accessible(g.db, g.user.id, int(student_id))
            except (ValueError, PermissionError):
                abort(403)

    @app.teardown_request
    def close(error):
        if hasattr(g, 'db'):
            g.db.rollback()
            g.db.close()

    @app.errorhandler(ValueError)
    def invalid(error):
        g.db.rollback()
        flash(str(error), 'error')
        return redirect(url_for('page', section='plan', student=g.student.id))

    @app.errorhandler(PermissionError)
    def denied(error):
        return str(error), 403

    @app.context_processor
    def common():
        pupils = list(g.db.scalars(select(User).where(User.teacher_id == g.user.id))) if g.user else []
        return {'csrf': session['csrf'], 'pupils': pupils, 'bot_username': settings.bot_username, 'base_url': settings.base_url.rstrip('/')}

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    @app.get('/login')
    def login():
        return render_template('login.html')

    @app.get('/auth/telegram')
    def auth():
        data = request.args.to_dict()
        if not verify_telegram(data, settings.bot_token):
            abort(403, 'Не удалось проверить вход через Telegram')
        try:
            telegram_id = int(data['id'])
        except (KeyError, ValueError):
            abort(400)
        user = g.db.scalar(select(User).where(User.telegram_id == telegram_id))
        if not user:
            user = User(telegram_id=telegram_id, name=data.get('first_name', ''))
            g.db.add(user)
            g.db.commit()
        session.clear()
        session['user_id'] = user.id
        session['csrf'] = secrets.token_urlsafe(24)
        session.permanent = True
        return redirect(url_for('page', section='plan'))

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.get('/')
    def index():
        return redirect(url_for('page', section='plan'))

    @app.get('/<section>')
    def page(section):
        if section not in ('plan', 'analytics', 'settings'):
            abort(404)
        today = datetime.now(ZoneInfo(g.student.timezone)).date()
        week = monday(date.fromisoformat(request.args.get('week', today.isoformat())))
        subjects = list(g.db.scalars(select(Subject).where(Subject.student_id == g.student.id)))
        tasks = list(g.db.scalars(select(Task).join(Subject).where(Subject.student_id == g.student.id)))
        slots = list(g.db.scalars(select(Slot).where(Slot.student_id == g.student.id, Slot.day >= week,
                        Slot.day < week+timedelta(days=7)).order_by(Slot.day, Slot.start)))
        goals = progress(g.db, g.student.id, week)
        invite = request.args.get('invite')
        data = analytics(g.db, g.user.id, g.student.id) if section == 'analytics' else None
        return render_template(f'{section}.html', section=section, subjects=subjects, tasks=tasks,
                               slots=slots, goals=goals, week=week, today=today, timedelta=timedelta,
                               invite=invite, data=data)

    def back(section='plan'):
        g.db.commit()
        return redirect(url_for('page', section=section, student=g.student.id))

    def owned(model, item_id):
        item = g.db.get(model, int(item_id))
        if not item:
            abort(404)
        if model is Task:
            subject_for(g.db, g.student.id, item.subject_id)
        elif item.student_id != g.student.id:
            abort(403)
        return item

    @app.post('/subjects')
    def subjects_create():
        name = request.form['name'].strip()[:80]
        if not name:
            raise ValueError('Укажите название предмета')
        g.db.add(Subject(student_id=g.student.id, name=name))
        return back('settings')

    @app.post('/tasks')
    def tasks_create():
        subject = subject_for(g.db, g.student.id, int(request.form['subject_id']))
        name = request.form['name'].strip()[:80]
        maximum = int(request.form['max_score'])
        part = request.form['part']
        if not name or not 1 <= maximum <= 100 or part not in ('test', 'second'):
            raise ValueError('Проверьте название, часть и максимальный балл')
        if g.db.scalar(select(Task).where(Task.subject_id == subject.id, Task.name == name)):
            raise ValueError('Такое задание уже есть')
        g.db.add(Task(subject_id=subject.id, name=name, max_score=maximum, part=part))
        return back('settings')

    @app.post('/tasks/<int:item_id>')
    def task_update(item_id):
        task = owned(Task, item_id)
        maximum = int(request.form['max_score'])
        if not 1 <= maximum <= 100:
            raise ValueError('Максимальный балл: от 1 до 100')
        name = request.form.get('name', task.name).strip()[:80]
        if not name: raise ValueError('Укажите название задания')
        if g.db.scalar(select(Task).where(Task.subject_id==task.subject_id,Task.name==name,Task.id!=task.id)):
            raise ValueError('Такое задание уже есть')
        task.name = name
        task.max_score = maximum
        return back('settings')

    @app.post('/scale/<int:item_id>')
    def scale_update(item_id):
        subject = owned(Subject, item_id)
        primary, secondary = request.form.getlist('primary'), request.form.getlist('secondary')
        if len(primary) != len(secondary):
            raise ValueError('Проверьте таблицу')
        subject.scale = parse_scale([(a, b) for a, b in zip(primary, secondary) if a.strip() or b.strip()])
        return back('settings')

    @app.post('/goals')
    def goal_create():
        subject = subject_for(g.db, g.student.id, int(request.form['subject_id']))
        kind = request.form['kind']
        task_id = int(request.form['task_id']) if request.form.get('task_id') else None
        target, weeks = int(request.form['target']), int(request.form['weeks'])
        if kind not in ('test', 'second', 'task') or not 1 <= target <= 1000 or not 1 <= weeks <= 52:
            raise ValueError('Проверьте параметры цели')
        if kind == 'task':
            task = owned(Task, task_id)
            if task.subject_id != subject.id:
                raise ValueError('Выберите задание выбранного предмета')
        else:
            task_id = None
        week = monday(date.fromisoformat(request.form['week']))
        series = uuid4().hex
        for n in range(weeks):
            g.db.add(Goal(student_id=g.student.id, subject_id=subject.id, task_id=task_id,
                          kind=kind, week=week+timedelta(weeks=n), target=target, series=series))
        return back()

    @app.post('/goals/<int:item_id>')
    def goal_update(item_id):
        target = int(request.form['target'])
        if not 1 <= target <= 1000:
            raise ValueError('Количество должно быть от 1 до 1000')
        change_series(g.db, Goal, g.user.id, g.student.id, item_id, request.form['scope'], {'target': target})
        return back()

    @app.post('/slots')
    def slot_create():
        day = date.fromisoformat(request.form['day'])
        start, end = request.form['start'], request.form['end']
        kind, title = request.form['kind'], request.form['title'].strip()[:120]
        datetime.strptime(start, '%H:%M'); datetime.strptime(end, '%H:%M')
        weeks = int(request.form['weeks'])
        if start >= end or not title or kind not in ('study', 'rest', 'exam') or not 1 <= weeks <= 52:
            raise ValueError('Проверьте время, название и повторение')
        subject_id = int(request.form['subject_id']) if request.form.get('subject_id') else None
        if subject_id:
            subject_for(g.db, g.student.id, subject_id)
        if kind == 'exam' and not subject_id:
            raise ValueError('Для пробника выберите предмет')
        task_ids = [int(x) for x in request.form.getlist('task_ids')]
        for task_id in task_ids:
            task = owned(Task, task_id)
            if task.subject_id != subject_id:
                raise ValueError('Задания должны относиться к выбранному предмету')
        series = uuid4().hex
        for n in range(weeks):
            g.db.add(Slot(student_id=g.student.id, day=day+timedelta(weeks=n), start=start, end=end,
                          kind=kind, title=title, subject_id=subject_id, task_ids=task_ids, series=series))
        return back()

    @app.post('/slots/<int:item_id>')
    def slot_update(item_id):
        slot = owned(Slot, item_id)
        start, end = request.form['start'], request.form['end']
        datetime.strptime(start, '%H:%M'); datetime.strptime(end, '%H:%M')
        if start >= end:
            raise ValueError('Окончание должно быть позже начала')
        change_series(g.db, Slot, g.user.id, g.student.id, item_id, request.form['scope'], {'start': start, 'end': end, 'day':date.fromisoformat(request.form.get('day',slot.day.isoformat())), 'title':request.form.get('title',slot.title).strip()[:120] or slot.title})
        return back()

    @app.post('/delete/<kind>/<int:item_id>')
    def remove(kind, item_id):
        model = {'goal': Goal, 'slot': Slot}.get(kind)
        if not model:
            abort(404)
        g.db.delete(owned(model, item_id))
        return back()

    @app.post('/activity/<int:item_id>/delete')
    def remove_activity(item_id):
        accessible(g.db, g.user.id, g.student.id, results=True)
        activity = owned(Activity, item_id)
        g.db.execute(delete(Score).where(Score.activity_id == activity.id))
        g.db.delete(activity)
        flash('Запись удалена. Исправленный результат можно внести в боте.')
        return back('analytics')

    @app.post('/profile')
    def profile():
        # Preferences always belong to the logged-in account, never to a pupil.
        user = g.user
        name, gender = request.form['name'].strip()[:80], request.form['gender']
        timezone, evening = request.form['timezone'], request.form['evening']
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            raise ValueError('Неизвестный часовой пояс')
        datetime.strptime(evening, '%H:%M')
        if not name or gender not in ('male', 'female', 'neutral'):
            raise ValueError('Проверьте имя и пол')
        user.name, user.gender, user.timezone, user.evening = name, gender, timezone, evening
        for key in ('reminders', 'wellbeing', 'share_wellbeing'):
            setattr(user, key, key in request.form)
        return back('settings')

    @app.post('/invite')
    def invite_create():
        token = secrets.token_urlsafe(18)
        g.db.add(Invite(token=token, teacher_id=g.user.id, expires=datetime.now(UTC).replace(tzinfo=None)+timedelta(days=7)))
        g.db.commit()
        return redirect(url_for('page', section='settings', invite=token))

    @app.post('/unlink')
    def unlink():
        g.user.teacher_id = None
        return back('settings')

    return app
