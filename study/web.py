import hashlib
import hmac
import secrets
import time
import json
from urllib.parse import urlsplit
from datetime import date, datetime, UTC, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import uuid4
from pathlib import Path
from flask import Flask, request, session, g, redirect, url_for, render_template, abort, flash, jsonify, Response
from sqlalchemy import select, delete, func, text
from sqlalchemy.exc import SQLAlchemyError, IntegrityError, OperationalError
from .avatars import fetch_avatar
from aiogram.exceptions import TelegramAPIError
from aiohttp import ClientError
from python_socks import ProxyError
from .presentation import display_label, task_order_key
from .groups import ensure_groups, group_tasks, save_group, attach_standard
from .catalog import CATALOG, add_subject, ensure_defaults
from .config import Settings, setup_logging
from .db import Database
from .access import access_allowed
from .models import User, Subject, Task, Goal, Slot, Activity, Score, Invite, TaskGroup, GroupTask, UserAvatar, GoalSelection, ExamTotal
from .services import accessible, subject_for, parse_scale, monday, progress, analytics, change_series, edit_activity
from .account import own_queries, delete_account, remove_catalog_item


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
    view_files = [Path(app.static_folder)/name for name in ('navigation.js','app.js','style.css')]
    view_files += sorted((Path(app.root_path)/app.template_folder).rglob('*.html'))
    view_version = hashlib.sha256(b''.join(path.read_bytes() for path in view_files)).hexdigest()[:16]
    app.config.update(SECRET_KEY=settings.secret_key, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=settings.cookie_secure,
                      MAX_CONTENT_LENGTH=128 * 1024, PERMANENT_SESSION_LIFETIME=timedelta(days=7))
    if settings.environment == 'production':
        app.config['TRUSTED_HOSTS'] = [urlsplit(settings.base_url).hostname, 'localhost', '127.0.0.1']
    app.jinja_env.filters["display_label"] = display_label
    db = Database(settings.database_url, initialize=settings.environment != 'production')
    app.extensions['db'] = db

    @app.before_request
    def prepare():
        g.nonce = secrets.token_urlsafe(24)
        if request.routing_exception is not None:
            raise request.routing_exception
        g.db = db.session()
        if request.endpoint in ('health', 'live'):
            return
        g.user = g.db.get(User, session.get('user_id')) if session.get('user_id') else None
        if g.user and request.endpoint not in ('static', 'logout') and not access_allowed(g.db, g.user.telegram_id):
            session.clear()
            abort(403, 'Доступ разрешён только пользователям из белого списка')
        session.setdefault('csrf', secrets.token_urlsafe(24))
        if request.endpoint == 'debug_login' and not settings.debug:
            abort(404)
        if request.method == 'POST' and not hmac.compare_digest(request.form.get('csrf', '').encode(), session['csrf'].encode()):
            abort(400, 'Обновите страницу и повторите действие')
        if request.endpoint not in ('login', 'auth', 'debug_login', 'static', 'health', 'service_worker') and not g.user:
            return redirect(url_for('login'))
        if g.user:
            student_id = str(g.user.id) if request.endpoint in ('export_account', 'remove_account') else request.values.get('student', str(g.user.id))
            try:
                g.student = accessible(g.db, g.user.id, int(student_id))
            except (ValueError, PermissionError):
                abort(403)

    @app.after_request
    def secure_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = (
            f"default-src 'self'; script-src 'self' 'nonce-{getattr(g, 'nonce', '')}' https://telegram.org; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' https: data:; "
            "frame-src https://oauth.telegram.org https://telegram.org; "
            "frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'")
        if request.endpoint == 'page':
            response.vary.add('X-Study-Navigation')
            response.vary.add('X-Study-Prefetch')
        if request.endpoint != 'static':
            response.headers['Cache-Control'] = 'no-store'
        if settings.environment == 'production':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.teardown_request
    def close(error):
        if hasattr(g, 'db'):
            g.db.rollback()
            g.db.close()

    @app.errorhandler(ValueError)
    def invalid(error):
        g.db.rollback()
        if request.method == 'POST' and request.headers.get('X-Study-Navigation') == '1':
            return jsonify(error=str(error)), 422
        flash(str(error), 'error')
        section = request.form.get('return_section', 'goals')
        if section not in ('goals','schedule','settings','analytics','editing'):
            section = 'goals'
        if not getattr(g, 'user', None):
            return redirect(url_for('login'))
        return redirect(url_for('page', section=section, student=g.student.id, tab=request.form.get('return_tab') if section == 'editing' else None))

    @app.errorhandler(OverflowError)
    def date_overflow(error):
        return invalid(ValueError('Дата вне допустимого диапазона'))

    @app.errorhandler(IntegrityError)
    def conflict(error):
        return invalid(ValueError('Данные изменились. Обновите страницу и повторите действие'))

    @app.errorhandler(OperationalError)
    def unavailable(error):
        g.db.rollback()
        app.logger.error('Database operation unavailable')
        return 'Сервис временно недоступен. Повторите действие позже', 503, {'Retry-After': '5'}

    @app.errorhandler(PermissionError)
    def denied(error):
        return str(error), 403

    @app.context_processor
    def common():
        pupils = list(g.db.scalars(select(User).where(User.teacher_id == g.user.id))) if g.user and g.user.is_teacher else []
        avatar = g.db.get(UserAvatar, g.user.id) if g.user else None
        return {'view_version': view_version, 'avatar_url': avatar.photo_url if avatar else '', 'avatar_fallback_url': url_for('profile_avatar') if settings.bot_token else '', 'csrf': session['csrf'], 'pupils': pupils, 'bot_username': settings.bot_username, 'base_url': settings.base_url.rstrip('/'), 'debug_login_enabled': settings.debug}

    @app.get('/profile/avatar')
    def profile_avatar():
        if not settings.bot_token:
            abort(404)
        try:
            result = fetch_avatar(settings, g.user.telegram_id)
        except (TelegramAPIError, ClientError, ProxyError, OSError, TimeoutError, ValueError):
            # Do not log Telegram exceptions: file URLs can contain the bot token.
            app.logger.warning('Avatar lookup unavailable user=%s', g.user.id)
            return '', 503
        if not result:
            app.logger.info('Telegram profile photo unavailable user=%s', g.user.id)
            abort(404)
        data, mime = result
        return Response(data, mimetype=mime)

    @app.get('/health')
    def health():
        try:
            g.db.execute(text('SELECT 1'))
            g.db.execute(select(User.id).limit(1))
        except SQLAlchemyError:
            app.logger.error('Database readiness failed')
            return {'status': 'unavailable'}, 503
        return {'status': 'ok'}

    @app.get('/live')
    def live():
        return {'status': 'ok'}

    @app.get('/sw.js')
    def service_worker():
        return app.send_static_file('sw.js')

    @app.get('/login')
    def login():
        session['login_state'] = secrets.token_urlsafe(32)
        return render_template('login.html', login_state=session['login_state'])

    @app.post('/auth/debug')
    def debug_login():
        user = g.db.scalar(select(User).order_by(User.id).limit(1))
        if user is not None and not access_allowed(g.db, user.telegram_id):
            abort(403, 'Доступ разрешён только пользователям из белого списка')
        if user is None:
            flash('Пока нет пользователей для входа', 'error')
            return redirect(url_for('login'))
        session.clear()
        session['user_id'] = user.id
        session['csrf'] = secrets.token_urlsafe(24)
        session.permanent = True
        return redirect(url_for('page', section='goals'))

    @app.get('/auth/telegram')
    def auth():
        data = request.args.to_dict()
        state = data.pop('state', '')
        expected = session.get('login_state', '')
        if not expected or not hmac.compare_digest(state.encode(), expected.encode()):
            abort(403, 'Откройте страницу входа и повторите авторизацию')
        if not verify_telegram(data, settings.bot_token):
            abort(403, 'Не удалось проверить вход через Telegram')
        try:
            telegram_id = int(data['id'])
        except (KeyError, ValueError):
            abort(400)
        if not access_allowed(g.db, telegram_id):
            abort(403, 'Доступ разрешён только пользователям из белого списка')
        user = g.db.scalar(select(User).where(User.telegram_id == telegram_id))
        if not user:
            user = User(telegram_id=telegram_id, name=data.get('first_name', ''))
            g.db.add(user)
            g.db.flush()
        photo_url = data.get('photo_url', '')
        try:
            parsed = urlsplit(photo_url)
            if len(photo_url) > 2048 or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                photo_url = ''
        except ValueError:
            photo_url = ''
        avatar = g.db.get(UserAvatar, user.id)
        if avatar:
            avatar.photo_url = photo_url
        elif photo_url:
            g.db.add(UserAvatar(user_id=user.id, photo_url=photo_url))
        g.db.commit()
        session.clear()
        session['user_id'] = user.id
        session['csrf'] = secrets.token_urlsafe(24)
        session.permanent = True
        return redirect(url_for('page', section='goals'))

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.get('/')
    def index():
        return redirect(url_for('page', section='goals'))

    @app.get('/<section>')
    def page(section):
        if section not in ('plan', 'goals', 'schedule', 'analytics', 'settings', 'editing'):
            abort(404)
        if section == 'plan':
            section = 'goals'
        ensure_defaults(g.db, g.student.id)
        g.db.commit()
        today = datetime.now(ZoneInfo(g.student.timezone)).date()
        week = monday(date.fromisoformat(request.args.get('week', today.isoformat())))
        if not date(2000, 1, 10) <= week <= date(2100, 1, 1):
            raise ValueError('Выберите неделю между 2000 и 2099 годами')
        subjects = list(g.db.scalars(select(Subject).where(Subject.student_id == g.student.id)))
        for subject in subjects:
            ensure_groups(g.db, subject.id)
        g.db.commit()
        groups = list(g.db.scalars(select(TaskGroup).join(Subject).where(Subject.student_id == g.student.id).order_by(TaskGroup.id)))
        memberships = {group.id: set(g.db.scalars(select(GroupTask.task_id).where(GroupTask.group_id == group.id))) for group in groups}
        tasks = sorted(g.db.scalars(select(Task).join(Subject).where(Subject.student_id == g.student.id)), key=lambda t: (t.subject_id, task_order_key(t)))
        slots = list(g.db.scalars(select(Slot).where(Slot.student_id == g.student.id, Slot.day >= week,
                        Slot.day < week+timedelta(days=7)).order_by(Slot.day, Slot.start)))
        goals = progress(g.db, g.student.id, week)
        invite = request.args.get('invite')
        period = int(request.args.get('period', '56'))
        if period not in (28, 56, 84, 365):
            raise ValueError('Выберите период аналитики')
        data = analytics(g.db, g.user.id, g.student.id, start=today-timedelta(days=period-1), end=today,
                         summaries=False) if section == 'analytics' else None
        tab = request.args.get('tab', 'works')
        if tab not in ('works', 'plan', 'subjects'):
            tab = 'works'
        records, record_scores, record_totals = [], {}, {}
        record_page = max(1, int(request.args.get('record_page', '1')))
        record_subject = request.args.get('record_subject', '')
        record_count = 0
        if section == 'editing' and tab == 'works':
            filters = [Activity.student_id == g.student.id]
            if record_subject:
                subject_for(g.db, g.student.id, int(record_subject))
                filters.append(Activity.subject_id == int(record_subject))
            record_count = g.db.scalar(select(func.count()).select_from(Activity).where(*filters))
            record_page = min(record_page, max(1, (record_count+49)//50))
            records = list(g.db.scalars(select(Activity).where(*filters).order_by(Activity.day.desc(), Activity.id.desc()).offset((record_page-1)*50).limit(50)))
            ids = [r.id for r in records if str(r.id) == request.args.get('edit_record') and g.user.id == g.student.id]
            if ids:
                for score in g.db.scalars(select(Score).where(Score.activity_id.in_(ids)).order_by(Score.task_id)):
                    record_scores.setdefault(score.activity_id, []).append(score)
                positions = {task.id: n for n, task in enumerate(tasks)}
                for rows in record_scores.values():
                    rows.sort(key=lambda row: positions[row.task_id])
                record_totals = {r.activity_id: r for r in g.db.scalars(select(ExamTotal).where(ExamTotal.activity_id.in_(ids)))}

        html = render_template('plan.html' if section in ('goals','schedule') else f'{section}.html', section=section, subjects=subjects, tasks=tasks,
                               slots=slots, goals=goals, week=week, today=today, timedelta=timedelta,
                               invite=invite, data=data, tab=tab, records=records, record_scores=record_scores, record_totals=record_totals, groups=groups, memberships=memberships, catalog=CATALOG,
                               catalog_by_name={item["label"]: item for item in CATALOG.values()},
                               period=period, record_page=record_page, record_subject=record_subject,
                               record_pages=max(1, (record_count+49)//50), record_count=record_count)
        if request.headers.get('X-Study-Navigation') == '1':
            return jsonify(html=html, url=request.full_path.rstrip('?'), user_id=g.user.id, version=view_version)
        return html

    @app.get('/activity/<int:activity_id>/editor')
    def activity_editor(activity_id):
        record = g.db.get(Activity, activity_id)
        if record is None:
            abort(404)
        if record.student_id != g.user.id or record.student_id != g.student.id:
            abort(403)
        today = datetime.now(ZoneInfo(g.user.timezone)).date()
        week = monday(date.fromisoformat(request.args.get('week', today.isoformat())))
        tasks = sorted(g.db.scalars(select(Task).where(Task.subject_id == record.subject_id)), key=task_order_key)
        positions = {task.id: index for index, task in enumerate(tasks)}
        scores = sorted(g.db.scalars(select(Score).where(Score.activity_id == record.id)), key=lambda row: positions[row.task_id])
        total = g.db.get(ExamTotal, record.id)
        html = render_template('activity_editor.html', section='editing', tab='works',
            record=record, record_scores={record.id: scores}, record_totals={record.id: total} if total else {},
            tasks=tasks, today=today, week=week, record_page=max(1, int(request.args.get('record_page', '1'))),
            record_subject=request.args.get('record_subject', ''))
        return jsonify(html=html, user_id=g.user.id, version=view_version)

    def back(section='goals'):
        if request.form.get('return_section') == 'editing':
            section = 'editing'
        week = request.form.get('return_week')
        if week:
            week = monday(date.fromisoformat(week)).isoformat()
        editing = '1' if request.form.get('editing') == '1' and section in ('goals','schedule') else None
        destination = url_for('page', section=section, student=g.student.id, week=week, edit=editing,
            tab=request.form.get('return_tab') if section == 'editing' else None,
            record_page=request.form.get('record_page') if section == 'editing' else None,
            record_subject=request.form.get('record_subject') if section == 'editing' else None)
        g.db.commit()
        return redirect(destination)

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
        return back('editing')

    @app.post('/subjects/<int:item_id>')
    def subject_update(item_id):
        subject = owned(Subject, item_id)
        name = request.form['name'].strip()[:80]
        if not name:
            raise ValueError('Укажите название предмета')
        if g.db.scalar(select(Subject).where(Subject.student_id == g.student.id, Subject.name == name, Subject.id != item_id)):
            raise ValueError('Такой предмет уже есть')
        subject.name = name
        return back('editing')

    @app.post('/subjects/catalog')
    def subject_from_catalog():
        subject, created = add_subject(g.db, g.student.id, request.form.get('catalog_key'))
        flash(f'{subject.name} добавлен' if created else 'Этот предмет уже добавлен')
        return back('editing')

    @app.post('/tasks')
    def tasks_create():
        subject = subject_for(g.db, g.student.id, int(request.form['subject_id']))
        name = request.form['name'].strip()[:80]
        maximum = int(request.form['max_score'])
        part = request.form['part']
        if not name or not 1 <= maximum <= 100 or part not in ('test', 'second', 'none'):
            raise ValueError('Проверьте название, часть и максимальный балл')
        if g.db.scalar(select(Task).where(Task.subject_id == subject.id, Task.name == name)):
            raise ValueError('Такое задание уже есть')
        task = Task(subject_id=subject.id, name=name, max_score=maximum, part=part)
        g.db.add(task)
        g.db.flush()
        attach_standard(g.db, task)
        return back('editing')

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
        return back('editing')

    @app.post('/groups')
    def group_create():
        subject = subject_for(g.db, g.student.id, int(request.form['subject_id']))
        save_group(g.db, subject.id, request.form['name'], request.form.getlist('task_ids'))
        return back('editing')

    @app.post('/groups/<int:item_id>')
    def group_update(item_id):
        group = g.db.get(TaskGroup, item_id)
        if not group:
            abort(404)
        subject_for(g.db, g.student.id, group.subject_id)
        save_group(g.db, group.subject_id, request.form['name'], request.form.getlist('task_ids'), group)
        return back('editing')

    @app.post('/scale/<int:item_id>')
    def scale_update(item_id):
        subject = owned(Subject, item_id)
        primary, secondary = request.form.getlist('primary'), request.form.getlist('secondary')
        if len(primary) != len(secondary):
            raise ValueError('Проверьте таблицу')
        subject.scale = parse_scale([(a, b) for a, b in zip(primary, secondary) if a.strip() or b.strip()])
        return back('editing')

    @app.post('/goals')
    def goal_create():
        subject = subject_for(g.db, g.student.id, int(request.form['subject_id']))
        kind = request.form['kind']
        task_id = int(request.form['task_id']) if request.form.get('task_id') else None
        target, weeks = int(request.form['target']), int(request.form['weeks'])
        if kind not in ('group', 'tasks', 'test', 'second', 'task') or not 1 <= target <= 1000 or not 1 <= weeks <= 52:
            raise ValueError('Проверьте параметры цели')
        group_id, task_ids, metric = None, [], 'tasks'
        if kind == 'group':
            if not request.form.get('group_id'):
                raise ValueError('Выберите группу заданий')
            group = g.db.get(TaskGroup, int(request.form['group_id']))
            if not group or group.subject_id != subject.id:
                raise ValueError('Выберите группу выбранного предмета')
            if not group_tasks(g.db, group.id):
                raise ValueError('В этой группе нет заданий')
            group_id = group.id
            metric = request.form.get('metric', 'tasks')
            if metric not in ('tasks', 'sets'):
                raise ValueError('Выберите способ подсчёта')
        elif kind == 'tasks':
            task_ids = sorted(set(map(int, request.form.getlist('task_ids'))))
            if not task_ids:
                raise ValueError('Выберите хотя бы одно задание')
            valid = set(g.db.scalars(select(Task.id).where(Task.subject_id == subject.id)))
            if not set(task_ids) <= valid:
                raise ValueError('Выберите задания выбранного предмета')
        if kind == 'task':
            task = owned(Task, task_id)
            if task.subject_id != subject.id:
                raise ValueError('Выберите задание выбранного предмета')
        else:
            task_id = None
        week = monday(date.fromisoformat(request.form['week']))
        series = uuid4().hex
        for n in range(weeks):
            goal = Goal(student_id=g.student.id, subject_id=subject.id, task_id=task_id,
                        kind=kind, week=week+timedelta(weeks=n), target=target, series=series)
            g.db.add(goal)
            if kind in ('group', 'tasks'):
                g.db.flush()
                g.db.add(GoalSelection(goal_id=goal.id, group_id=group_id, task_ids=list(task_ids), metric=metric))
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
        return back('schedule')

    @app.post('/slots/<int:item_id>')
    def slot_update(item_id):
        slot = owned(Slot, item_id)
        start, end = request.form['start'], request.form['end']
        datetime.strptime(start, '%H:%M'); datetime.strptime(end, '%H:%M')
        if start >= end:
            raise ValueError('Окончание должно быть позже начала')
        change_series(g.db, Slot, g.user.id, g.student.id, item_id, request.form['scope'], {'start': start, 'end': end, 'day':date.fromisoformat(request.form.get('day',slot.day.isoformat())), 'title':request.form.get('title',slot.title).strip()[:120] or slot.title})
        return back('schedule')

    @app.post('/delete/<kind>/<int:item_id>')
    def remove(kind, item_id):
        model = {'goal': Goal, 'slot': Slot}.get(kind)
        if not model:
            abort(404)
        g.db.delete(owned(model, item_id))
        return back('schedule' if kind == 'slot' else 'goals')

    @app.post('/activity/<int:item_id>')
    def update_activity(item_id):
        accessible(g.db, g.user.id, g.student.id, results=True)
        activity = owned(Activity, item_id)
        scores = list(g.db.scalars(select(Score).where(Score.activity_id == item_id)))
        values = {}
        for score in scores:
            raw = request.form.get(f'score_{score.task_id}')
            if raw is None:
                raise ValueError('Укажите результат каждого задания')
            values[score.task_id] = int(raw) if raw.strip() else None
        raw_primary, raw_secondary = request.form.get('primary', ''), request.form.get('secondary', '')
        if request.form.get('conversion') == 'auto':
            raw_secondary = ''
        elif request.form.get('conversion') not in (None, 'manual'):
            raise ValueError('Выберите способ перевода баллов')
        edit_activity(g.db, g.user.id, g.student.id, activity.id,
                      date.fromisoformat(request.form['day']), request.form.get('title', ''), values,
                      int(raw_primary) if raw_primary.strip() else None,
                      int(raw_secondary) if raw_secondary.strip() else None)
        flash('Запись сохранена')
        return back('editing')

    @app.post('/activity/<int:item_id>/delete')
    def remove_activity(item_id):
        accessible(g.db, g.user.id, g.student.id, results=True)
        activity = owned(Activity, item_id)
        g.db.execute(delete(Score).where(Score.activity_id == activity.id))
        g.db.delete(activity)
        flash('Запись удалена')
        return back('editing')

    @app.get('/account/export')
    def export_account():
        queries = own_queries(g.user)
        def chunks():
            with db.session() as s:
                yield '{'
                for index, (model, condition) in enumerate(queries):
                    if index: yield ','
                    yield json.dumps(model.__tablename__) + ':['
                    for n, row in enumerate(s.scalars(select(model).where(condition).execution_options(yield_per=100))):
                        if n: yield ','
                        yield json.dumps({column.name: getattr(row, column.name) for column in model.__table__.columns}, ensure_ascii=False, default=str)
                    yield ']'
                yield '}'
        return Response(chunks(), mimetype='application/json', headers={'Content-Disposition': 'attachment; filename="study-data.json"'})

    @app.post('/account/delete')
    def remove_account():
        if request.form.get('confirmation') != 'УДАЛИТЬ':
            raise ValueError('Введите УДАЛИТЬ для подтверждения удаления аккаунта')
        delete_account(g.db, g.user)
        g.db.commit()
        session.clear()
        return redirect(url_for('login'))

    @app.post('/catalog/<kind>/<int:item_id>/delete')
    def delete_catalog_item(kind, item_id):
        model = {'subject': Subject, 'task': Task, 'group': TaskGroup}.get(kind)
        if model is None: abort(404)
        if model is TaskGroup:
            item = g.db.get(model, item_id)
            if item is None: abort(404)
            subject_for(g.db, g.student.id, item.subject_id)
            if item.kind:
                raise ValueError('Стандартную группу можно редактировать, но нельзя удалить')
        else:
            item = owned(model, item_id)
        remove_catalog_item(g.db, g.student.id, model, item)
        flash('Запись удалена')
        return back('editing')

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
        if not g.user.is_teacher:
            abort(403)
        token = secrets.token_urlsafe(18)
        g.db.add(Invite(token=token, teacher_id=g.user.id, expires=datetime.now(UTC).replace(tzinfo=None)+timedelta(days=7)))
        g.db.commit()
        return redirect(url_for('page', section='settings', invite=token))

    @app.post('/unlink')
    def unlink():
        g.user.teacher_id = None
        return back('settings')

    return app
