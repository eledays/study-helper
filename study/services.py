from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo
from uuid import uuid4
from sqlalchemy import select, delete
from .presentation import display_label, task_order_key
from .messages import bot_html, bot_text, bot_texts, UserMessageError
from .groups import standard_tasks, group_tasks
from .models import User, Subject, Task, Goal, Slot, Activity, Score, Daily, ExamTotal, GoalSelection, TaskGroup


def monday(day):
    return day - timedelta(days=day.weekday())


def accessible(s, actor_id, student_id, results=False):
    student = s.get(User, student_id)
    if not student or (actor_id != student_id and (results or student.teacher_id != actor_id or not (actor := s.get(User, actor_id)) or not actor.is_teacher)):
        raise PermissionError(bot_text('errors.no_access'))
    return student


def subject_for(s, student_id, subject_id):
    subject = s.get(Subject, subject_id)
    if not subject or subject.student_id != student_id:
        raise UserMessageError(bot_text('errors.subject_not_found'))
    return subject


def parse_scale(rows):
    result = {}
    for row in rows:
        if len(row) != 2:
            raise ValueError('В каждой строке нужны два балла')
        a, b = map(int, row)
        if a < 0 or not 0 <= b <= 100 or str(a) in result:
            raise ValueError('Проверьте баллы и повторяющиеся строки')
        result[str(a)] = b
    ordered = sorted((int(a), b) for a, b in result.items())
    if any(b > d for (_, b), (_, d) in zip(ordered, ordered[1:])):
        raise ValueError('Шкала не должна убывать')
    return result


def record_activity(s, actor_id, student_id, subject_id, day, kind, values, request_key, title=''):
    student = accessible(s, actor_id, student_id, results=True)
    subject = subject_for(s, student_id, subject_id)
    if kind not in ('practice', 'test', 'exam') or not values:
        raise UserMessageError(bot_text('errors.select_tasks'))
    if day > datetime.now(ZoneInfo(student.timezone)).date():
        raise UserMessageError(bot_text('errors.future_result'))
    existing = s.scalar(select(Activity).where(Activity.request_key == request_key))
    if existing:
        if existing.student_id != student_id:
            raise PermissionError(bot_text('errors.no_access'))
        return existing
    tasks = list(s.scalars(select(Task).where(Task.subject_id == subject_id).order_by(Task.id)))
    task_map = {t.id: t for t in tasks}
    normalized = {int(k): v for k, v in values.items()}
    if any(k not in task_map for k in normalized):
        raise UserMessageError(bot_text('errors.foreign_task'))
    if kind == 'test' and set(normalized) != {t.id for t in standard_tasks(s, subject_id, 'test')}:
        raise UserMessageError(bot_text('errors.full_test'))
    if kind == 'exam':
        normalized = {t.id: normalized.get(t.id) for t in tasks}
    prepared = []
    total = 0
    for task_id, value in normalized.items():
        task = task_map[task_id]
        attempted = value is not None
        score = int(value) if attempted else 0
        if not 0 <= score <= task.max_score:
            raise UserMessageError(bot_text('errors.score_range'))
        if kind == 'practice' and not attempted:
            raise UserMessageError(bot_text('errors.practice_score'))
        prepared.append((task, score, attempted))
        total += score
    activity = Activity(student_id=student_id, subject_id=subject_id, day=day, kind=kind,
                        title=title[:120], request_key=request_key, scale_snapshot=dict(subject.scale) if kind == 'exam' else {})
    s.add(activity)
    s.flush()
    for task, score, attempted in prepared:
        s.add(Score(activity_id=activity.id, task_id=task.id, score=score,
                    maximum=task.max_score, attempted=attempted))
    if kind == 'exam':
        activity.secondary = subject.scale.get(str(total))
    daily = s.scalar(select(Daily).where(Daily.student_id == student_id, Daily.day == day))
    if not daily:
        s.add(Daily(student_id=student_id, day=day, status='study'))
    else:
        daily.status = 'study'
    s.flush()
    return activity


def record_exam_total(s, actor_id, student_id, subject_id, day, primary, request_key, title=''):
    student = accessible(s, actor_id, student_id, results=True)
    subject = subject_for(s, student_id, subject_id)
    if day > datetime.now(ZoneInfo(student.timezone)).date():
        raise UserMessageError(bot_text('errors.future_result'))
    existing = s.scalar(select(Activity).where(Activity.request_key == request_key))
    if existing:
        if existing.student_id != student_id:
            raise PermissionError(bot_text('errors.no_access'))
        return existing
    maximum = sum(t.max_score for t in s.scalars(select(Task).where(Task.subject_id == subject_id)))
    if not maximum or not 0 <= primary <= maximum:
        raise UserMessageError(bot_text('errors.primary_range', maximum=maximum))
    activity = Activity(student_id=student_id, subject_id=subject_id, day=day, kind='exam',
                        title=title[:120], request_key=request_key, scale_snapshot=dict(subject.scale),
                        secondary=subject.scale.get(str(primary)))
    s.add(activity)
    s.flush()
    s.add(ExamTotal(activity_id=activity.id, primary_score=primary, maximum=maximum))
    daily = s.scalar(select(Daily).where(Daily.student_id == student_id, Daily.day == day))
    if daily:
        daily.status = 'study'
    else:
        s.add(Daily(student_id=student_id, day=day, status='study'))
    s.flush()
    return activity


def progress(s, student_id, week):
    goals = list(s.scalars(select(Goal).where(Goal.student_id == student_id, Goal.week == monday(week))))
    activities = list(s.scalars(select(Activity).where(Activity.student_id == student_id,
        Activity.day >= monday(week), Activity.day < monday(week) + timedelta(days=7), Activity.kind != 'exam')))
    scores = list(s.scalars(select(Score).where(Score.activity_id.in_([a.id for a in activities])))) if activities else []
    by_id = {a.id: a for a in activities}
    tasks = {t.id: t for t in s.scalars(select(Task).join(Subject).where(Subject.student_id == student_id))}
    subjects = {x.id: display_label(x.name) for x in s.scalars(select(Subject).where(Subject.student_id == student_id).order_by(Subject.id))}
    result = []
    for g in goals:
        if g.kind in ('group', 'tasks'):
            selection = s.get(GoalSelection, g.id)
            group = s.get(TaskGroup, selection.group_id) if selection and selection.group_id else None
            selected_ids = {t.id for t in group_tasks(s, group.id)} if group else set(selection.task_ids if selection else [])
            relevant = [sc for sc in scores if sc.attempted and sc.task_id in selected_ids and by_id[sc.activity_id].subject_id == g.subject_id]
            if group and selection.metric == 'sets':
                attempted = {}
                for sc in relevant:
                    attempted.setdefault(sc.activity_id, set()).add(sc.task_id)
                count = sum(bool(selected_ids) and selected_ids <= ids for ids in attempted.values())
            else:
                count = len(relevant)
            label = group.name if group else ', '.join(tasks[task_id].name for task_id in sorted(selected_ids) if task_id in tasks) or bot_text('goals.tasks_label')
        elif g.kind == 'test':
            required = {t.id for t in standard_tasks(s, g.subject_id, 'test')}
            count = sum(a.subject_id == g.subject_id and a.kind == 'test' and required <= {sc.task_id for sc in scores if sc.activity_id == a.id and sc.attempted} for a in activities)
            label = bot_text('goals.test_label')
        else:
            second_ids = {t.id for t in standard_tasks(s, g.subject_id, 'second')} if g.kind != 'task' else set()
            count = sum(sc.attempted and by_id[sc.activity_id].subject_id == g.subject_id and
                        (sc.task_id == g.task_id if g.kind == 'task' else sc.task_id in second_ids) for sc in scores)
            label = tasks[g.task_id].name if g.kind == 'task' else bot_text('goals.second_label')
        result.append({'id': g.id, 'label': f'{subjects[g.subject_id]}. {label}', 'done': count,
                       'target': g.target, 'percent': min(100, round(count / g.target * 100))})
    return result


def change_series(s, model, actor_id, student_id, item_id, scope, changes):
    accessible(s, actor_id, student_id)
    item = s.get(model, item_id)
    if not item or item.student_id != student_id:
        raise ValueError('Запись не найдена')
    if scope not in ('one', 'future', 'all'):
        raise ValueError('Выберите область изменения')
    rows = [item] if scope == 'one' else list(s.scalars(select(model).where(model.student_id == student_id, model.series == item.series)))
    anchor = item.week if model is Goal else item.day
    for row in rows:
        when = row.week if model is Goal else row.day
        if scope == 'future' and when < anchor:
            continue
        for key, value in changes.items():
            if key == 'day':
                row.day = when + (value - anchor)
            else:
                setattr(row, key, value)


def analytics(s, actor_id, student_id, start=None, end=None, summaries=True):
    user = accessible(s, actor_id, student_id)
    filters = [Activity.student_id == student_id]
    if start is not None: filters.append(Activity.day >= start)
    if end is not None: filters.append(Activity.day <= end)
    activities = list(s.scalars(select(Activity).where(*filters).order_by(Activity.day, Activity.id)))
    tasks = sorted(s.scalars(select(Task).join(Subject).where(Subject.student_id == student_id)), key=lambda t: (t.subject_id, task_order_key(t)))
    subjects = {x.id: display_label(x.name) for x in s.scalars(select(Subject).where(Subject.student_id == student_id).order_by(Subject.id))}
    task_map = {t.id: t for t in tasks}
    by_task = {}
    by_activity = {}
    scores = list(s.scalars(select(Score).where(Score.activity_id.in_([a.id for a in activities])))) if activities else []
    by_id = {a.id: a for a in activities}
    for score in scores:
        by_task.setdefault(score.task_id, []).append(score)
        by_activity.setdefault(score.activity_id, []).append(score)
    exams = []
    for a in activities:
        if a.kind == 'exam':
            rows = by_activity.get(a.id, [])
            total = s.get(ExamTotal, a.id)
            exams.append({'id': a.id, 'day': a.day.isoformat(), 'subject': subjects[a.subject_id], 'subject_id': a.subject_id,
                          'title': a.title or 'Пробник', 'primary': total.primary_score if total else sum(r.score for r in rows),
                          'maximum': total.maximum if total else sum(r.maximum for r in rows), 'secondary': a.secondary,
                          'tasks': [{'name': task_map[r.task_id].name, 'score': r.score,
                                     'max': r.maximum, 'attempted': r.attempted} for r in rows]})
    stats = []
    for t in tasks:
        for kind in ('practice', 'exam'):
            rows = [r for r in by_task.get(t.id, []) if
                    ((by_id[r.activity_id].kind == 'exam') == (kind == 'exam'))]
            attempts = [r for r in rows if r.attempted]
            if rows:
                stats.append({'id': t.id, 'subject_id': t.subject_id, 'task_name': t.name,
                              'name': f'{subjects[t.subject_id]}. {t.name}', 'kind': kind,
                              'attempts': len(attempts),
                              'average': round(sum(r.score for r in attempts)/len(attempts), 2) if attempts else 0,
                              'percent': round(sum(r.score/r.maximum for r in attempts)/len(attempts)*100) if attempts else 0,
                              'results': [{'day': by_id[r.activity_id].day.isoformat(),
                                           'activity_id': r.activity_id, 'score': r.score, 'maximum': r.maximum,
                                           'attempted': r.attempted} for r in sorted(rows, key=lambda r: (by_id[r.activity_id].day, r.activity_id))]})
    days = []
    if actor_id == student_id or user.share_wellbeing:
        days = [{'day': d.day.isoformat(), 'mood': d.mood, 'load': d.load} for d in s.scalars(
            select(Daily).where(Daily.student_id == student_id, (Daily.mood.is_not(None) | Daily.load.is_not(None)),
                Daily.day >= (start or date.min), Daily.day <= (end or date.max)).order_by(Daily.day))]
    history = [{'id': a.id, 'day': a.day.isoformat(), 'subject':subjects[a.subject_id],
                'kind':a.kind, 'attempts':sum(r.attempted for r in scores if r.activity_id==a.id),
                'score':sum(r.score for r in scores if r.activity_id==a.id)} for a in activities if summaries and a.kind != 'exam']
    weeks = sorted({monday(a.day) for a in activities if a.kind != 'exam'} |
                   {g.week for g in s.scalars(select(Goal).where(Goal.student_id == student_id))}) if summaries else []
    weekly = [{'day':w.isoformat(), 'attempts':sum(r.attempted for r in scores if by_id[r.activity_id].kind != 'exam' and monday(by_id[r.activity_id].day)==w),
               'goals':progress(s,student_id,w)} for w in weeks]
    return {'exams': exams, 'tasks': stats, 'wellbeing': days, 'history':history, 'weekly':weekly,
            'subjects': [{'id': key, 'name': value} for key, value in subjects.items()],
            'task_catalog': [{'id': t.id, 'subject_id': t.subject_id, 'name': t.name} for t in tasks],
            'rhythm': [{'id': a.id, 'day': a.day.isoformat(), 'subject_id': a.subject_id, 'kind': a.kind} for a in activities]}



def goal_cards(rows, weekly=False):
    cards = []
    for row in rows:
        percent = max(0, min(100, row['percent']))
        target = 5 if weekly else 10
        filled = min(target, int(percent * target / 100 + 0.5))
        bar = bot_text('goals.filled') * filled + bot_text('goals.remaining') * (target - filled)
        cards.append(bot_html('weekly.goal_card', label=row['label'], percent=percent, bar=bar, done=row['done'], target=row['target']) if weekly else bot_html('goals.progress', label=row['label'], percent=percent, bar=bar))
    return ('\n' if weekly else '\n\n').join(cards) if cards else bot_html('goals.empty')


def weekly_text(s, student_id, day):
    start = monday(day)
    study_days = len(set(s.scalars(select(Activity.day).where(Activity.student_id == student_id, Activity.day >= start, Activity.day <= day))))
    sections = [bot_html('weekly.heading'), bot_html('weekly.goals') + '\n' + goal_cards(progress(s, student_id, day), weekly=True),
        bot_html('weekly.statistics') + '\n' + bot_html('weekly.study_days', study_days=study_days, rest_days=(day-start).days+1-study_days)]
    subjects = {x.id: display_label(x.name) for x in s.scalars(select(Subject).where(Subject.student_id == student_id))}
    exams = [{'day': a.day.isoformat(), 'subject': subjects[a.subject_id], 'secondary': a.secondary}
             for a in s.scalars(select(Activity).where(Activity.student_id == student_id, Activity.kind == 'exam',
                 Activity.day >= start, Activity.day <= day).order_by(Activity.day, Activity.id))]
    if exams:
        sections.append(bot_html('weekly.exams') + '\n' + '\n'.join(
            bot_html('weekly.exam', weekday=bot_texts('weekly.weekdays')[date.fromisoformat(a['day']).weekday()], subject=a['subject'], score=bot_text('weekly.exam_score', score=a['secondary']) if a['secondary'] is not None else bot_text('weekly.no_scale')) for a in exams))
    variants = bot_texts('weekly.encouragement_active' if study_days else 'weekly.encouragement_rest')
    # Stable within one week, advances each week without requiring stored state
    sections.append(variants[(start.toordinal() // 7 + student_id) % len(variants)])
    return '\n\n'.join(sections)


def edit_activity(s, actor_id, student_id, activity_id, day, title, values=None, primary=None, secondary=None):
    student = accessible(s, actor_id, student_id, results=True)
    activity = s.get(Activity, activity_id)
    if not activity or activity.student_id != student_id:
        raise PermissionError(bot_text('errors.no_access'))
    if day > datetime.now(ZoneInfo(student.timezone)).date():
        raise UserMessageError(bot_text('errors.future_result'))
    if secondary is not None and not 0 <= secondary <= 100:
        raise ValueError('Балл по шкале: от 0 до 100')
    total = s.get(ExamTotal, activity.id)
    scores = list(s.scalars(select(Score).where(Score.activity_id == activity.id)))
    if total:
        if primary is None or not 0 <= primary <= total.maximum:
            raise UserMessageError(bot_text('errors.primary_range', maximum=total.maximum))
        calculated = primary
    else:
        if values is None or set(values) != {r.task_id for r in scores}:
            raise ValueError('Укажите результат каждого задания')
        if any(v is not None and not 0 <= v <= r.maximum for r in scores for v in [values[r.task_id]]):
            raise UserMessageError(bot_text('errors.score_range'))
        calculated = sum(v or 0 for v in values.values())
    activity.day, activity.title = day, title.strip()[:120]
    if total:
        total.primary_score = primary
    else:
        for score in scores:
            value = values[score.task_id]
            score.score, score.attempted = value or 0, value is not None
    if activity.kind == 'exam':
        activity.secondary = secondary if secondary is not None else activity.scale_snapshot.get(str(calculated))
    daily = s.scalar(select(Daily).where(Daily.student_id == student_id, Daily.day == day))
    if not daily:
        s.add(Daily(student_id=student_id, day=day, status='study'))
    return activity
