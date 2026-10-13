from datetime import date, timedelta
from uuid import uuid4
from sqlalchemy import select, delete
from .models import User, Subject, Task, Goal, Slot, Activity, Score, Daily


def monday(day):
    return day - timedelta(days=day.weekday())


def accessible(s, actor_id, student_id, results=False):
    student = s.get(User, student_id)
    if not student or (actor_id != student_id and (results or student.teacher_id != actor_id)):
        raise PermissionError('Нет доступа')
    return student


def subject_for(s, student_id, subject_id):
    subject = s.get(Subject, subject_id)
    if not subject or subject.student_id != student_id:
        raise ValueError('Предмет не найден')
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
    accessible(s, actor_id, student_id, results=True)
    subject = subject_for(s, student_id, subject_id)
    if kind not in ('practice', 'test', 'exam') or not values:
        raise ValueError('Выберите задания')
    if day > date.today() + timedelta(days=1):
        raise ValueError('Результат нельзя внести за будущую дату')
    existing = s.scalar(select(Activity).where(Activity.request_key == request_key))
    if existing:
        if existing.student_id != student_id:
            raise PermissionError('Нет доступа')
        return existing
    tasks = list(s.scalars(select(Task).where(Task.subject_id == subject_id).order_by(Task.id)))
    task_map = {t.id: t for t in tasks}
    normalized = {int(k): v for k, v in values.items()}
    if any(k not in task_map for k in normalized):
        raise ValueError('Задание другого предмета')
    if kind == 'test' and set(normalized) != {t.id for t in tasks if t.part == 'test'}:
        raise ValueError('Полная тестовая часть должна включать все её задания')
    if kind == 'exam':
        normalized = {t.id: normalized.get(t.id) for t in tasks}
    activity = Activity(student_id=student_id, subject_id=subject_id, day=day, kind=kind,
                        title=title[:120], request_key=request_key, scale_snapshot=dict(subject.scale) if kind == 'exam' else {})
    s.add(activity)
    s.flush()
    total = 0
    for task_id, value in normalized.items():
        task = task_map[task_id]
        attempted = value is not None
        score = int(value) if attempted else 0
        if not 0 <= score <= task.max_score:
            raise ValueError('Балл вне диапазона')
        if kind != 'exam' and not attempted:
            raise ValueError('Для практики укажите балл')
        s.add(Score(activity_id=activity.id, task_id=task.id, score=score,
                    maximum=task.max_score, attempted=attempted))
        total += score
    if kind == 'exam':
        activity.secondary = subject.scale.get(str(total))
    daily = s.scalar(select(Daily).where(Daily.student_id == student_id, Daily.day == day))
    if not daily:
        s.add(Daily(student_id=student_id, day=day, status='study'))
    else:
        daily.status = 'study'
    s.flush()
    return activity


def progress(s, student_id, week):
    goals = list(s.scalars(select(Goal).where(Goal.student_id == student_id, Goal.week == monday(week))))
    activities = list(s.scalars(select(Activity).where(Activity.student_id == student_id,
        Activity.day >= monday(week), Activity.day < monday(week) + timedelta(days=7), Activity.kind != 'exam')))
    scores = list(s.scalars(select(Score).where(Score.activity_id.in_([a.id for a in activities])))) if activities else []
    by_id = {a.id: a for a in activities}
    tasks = {t.id: t for t in s.scalars(select(Task).join(Subject).where(Subject.student_id == student_id))}
    subjects = {x.id: x.name for x in s.scalars(select(Subject).where(Subject.student_id == student_id))}
    result = []
    for g in goals:
        if g.kind == 'test':
            count = sum(a.subject_id == g.subject_id and a.kind == 'test' for a in activities)
            label = 'Тестовая часть'
        else:
            count = sum(sc.attempted and by_id[sc.activity_id].subject_id == g.subject_id and
                        (sc.task_id == g.task_id if g.kind == 'task' else tasks[sc.task_id].part == 'second') for sc in scores)
            label = tasks[g.task_id].name if g.kind == 'task' else 'Любые задания второй части'
        result.append({'id': g.id, 'label': f'{subjects[g.subject_id]} · {label}', 'done': count,
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
            setattr(row, key, value)


def analytics(s, actor_id, student_id):
    user = accessible(s, actor_id, student_id)
    activities = list(s.scalars(select(Activity).where(Activity.student_id == student_id).order_by(Activity.day, Activity.id)))
    tasks = list(s.scalars(select(Task).join(Subject).where(Subject.student_id == student_id)))
    subjects = {x.id: x.name for x in s.scalars(select(Subject).where(Subject.student_id == student_id))}
    scores = list(s.scalars(select(Score).where(Score.activity_id.in_([a.id for a in activities])))) if activities else []
    by_id = {a.id: a for a in activities}
    exams = []
    for a in activities:
        if a.kind == 'exam':
            rows = [r for r in scores if r.activity_id == a.id]
            exams.append({'id': a.id, 'day': a.day.isoformat(), 'subject': subjects[a.subject_id],
                          'title': a.title or 'Пробник', 'primary': sum(r.score for r in rows),
                          'maximum': sum(r.maximum for r in rows), 'secondary': a.secondary,
                          'tasks': [{'name': next(t.name for t in tasks if t.id == r.task_id), 'score': r.score,
                                     'max': r.maximum, 'attempted': r.attempted} for r in rows]})
    stats = []
    for t in tasks:
        for kind in ('practice', 'exam'):
            rows = [r for r in scores if r.task_id == t.id and r.attempted and
                    ((by_id[r.activity_id].kind == 'exam') == (kind == 'exam'))]
            if rows:
                stats.append({'name': f'{subjects[t.subject_id]} · {t.name}', 'kind': kind, 'attempts': len(rows),
                              'average': round(sum(r.score for r in rows)/len(rows), 2),
                              'percent': round(sum(r.score/r.maximum for r in rows)/len(rows)*100)})
    days = []
    if actor_id == student_id or user.share_wellbeing:
        days = [{'day': d.day.isoformat(), 'mood': d.mood, 'load': d.load} for d in s.scalars(
            select(Daily).where(Daily.student_id == student_id).order_by(Daily.day))]
    return {'exams': exams, 'tasks': stats, 'wellbeing': days}


def weekly_text(s, student_id, day):
    rows = progress(s, student_id, day)
    lines = ['Неделя подходит к концу. Вот что получилось:']
    lines += [f"• {r['label']}: {r['done']} из {r['target']}" for r in rows]
    start = monday(day)
    reports = list(s.scalars(select(Daily).where(Daily.student_id == student_id, Daily.day >= start, Daily.day <= day)))
    lines.append(f'Заполнено дней: {len(reports)} из {(day-start).days+1}.')
    exams = [a for a in analytics(s, student_id, student_id)['exams'] if start.isoformat() <= a['day'] <= day.isoformat()]
    lines += [f"Пробник · {a['subject']}: {a['primary']} первичных" + (f", {a['secondary']} вторичных" if a['secondary'] is not None else '') for a in exams]
    lines.append('План на следующую неделю можно посмотреть на сайте.')
    return '\n'.join(lines)
