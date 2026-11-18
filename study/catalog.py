"""ЕГЭ 2027, материалы ФИПИ. Профильная математика: модель 2026 года.

Источники и статус шкал: docs/exam-catalog.md
Неподтверждённые шкалы не используются для расчёта вторичных баллов
"""
from sqlalchemy import select
from .models import Subject, Task, UserSetup


def scores(count, default=1, overrides=None):
    return [(overrides or {}).get(n, default) for n in range(1, count + 1)]


CATALOG = {
    'russian': {'name': 'Русский язык', 'first': 26,
                'scores': scores(27, overrides={8: 2, 22: 2, 27: 22}), 'code': 'ru'},
    'math': {'name': 'Математика · профиль', 'first': 12,
             'scores': [1] * 12 + [2, 3, 2, 2, 3, 4, 4], 'code': 'ma'},
    'physics': {'name': 'Физика', 'first': 20,
                'scores': scores(26, overrides={**dict.fromkeys([5, 6, 9, 10, 14, 15, 17, 18], 2),
                                               21: 3, 22: 2, 23: 2, 24: 3, 25: 3, 26: 4}), 'code': 'fi'},
    'chemistry': {'name': 'Химия', 'first': 28,
                  'scores': scores(34, overrides={**dict.fromkeys([6, 7, 8, 14, 15, 22, 23, 24], 2),
                                                 29: 2, 30: 2, 31: 4, 32: 5, 33: 3, 34: 4}), 'code': 'hi'},
    'informatics': {'name': 'Информатика', 'first': 27,
                    'scores': [1] * 25 + [2, 2], 'code': 'inf'},
    'biology': {'name': 'Биология', 'first': 21,
                'scores': scores(21, 2, dict.fromkeys([1, 3, 4, 5, 9, 13, 21], 1)) + [3, 3, 3, 3, 4, 3, 3], 'code': 'bi'},
    'history': {'name': 'История', 'first': 12,
                'scores': [2, 1, 2, 3, 2, 2, 2, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 3, 3, 2, 3, 2], 'code': 'is'},
    'social': {'name': 'Обществознание', 'first': 16,
               'scores': scores(16, 2, dict.fromkeys([1, 3, 9, 12], 1)) + [2, 2, 3, 3, 3, 4, 3, 4, 6], 'code': 'ob'},
}
for item in CATALOG.values():
    item['label'] = item['name'] + ' · ЕГЭ 2027'
    item['source'] = f"https://doc.fipi.ru/ege/demoversii-specifikacii-kodifikatory/2027/{item['code']}_11_2027.zip"


def add_subject(s, student_id, key):
    if key not in CATALOG:
        raise ValueError('Выберите предмет из каталога')
    item = CATALOG[key]
    existing = s.scalar(select(Subject).where(Subject.student_id == student_id, Subject.name == item['label']))
    if existing:
        return existing, False
    subject = Subject(student_id=student_id, name=item['label'], scale={})
    s.add(subject)
    s.flush()
    for n, maximum in enumerate(item['scores'], 1):
        s.add(Task(subject_id=subject.id, name=f'№{n}', part='test' if n <= item['first'] else 'second', max_score=maximum))
    from .groups import ensure_groups
    s.flush()
    ensure_groups(s, subject.id)
    return subject, True


def ensure_defaults(s, student_id):
    if s.get(UserSetup, student_id):
        return
    if not s.scalar(select(Subject.id).where(Subject.student_id == student_id).limit(1)):
        for key in ('russian', 'math'):
            add_subject(s, student_id, key)
    s.add(UserSetup(user_id=student_id))
    s.flush()


def migrate_project_math(s):
    """Renumber the withdrawn 20-task draft without changing referenced task IDs.

    Draft-only tasks stay as historical tasks. Their scores and plan references
    remain readable, but they no longer belong to the current exam.
    """
    from .models import GroupTask, TaskGroup

    label = CATALOG['math']['label']
    mapping = {**{n: n for n in range(1, 6)},
               **{n: n - 1 for n in range(7, 13)},
               **{n: n - 1 for n in range(14, 17)},
               **{n: n - 1 for n in range(18, 21)}}
    for subject in s.scalars(select(Subject).where(Subject.name == label)):
        tasks = list(s.scalars(select(Task).where(Task.subject_id == subject.id)))
        numbered = {int(t.name[1:]): t for t in tasks if t.name.startswith('№') and t.name[1:].isdigit()}
        # Only migrate untouched draft catalogs. A customized catalog needs a
        # deliberate manual mapping, so never guess from its task numbers.
        if len(tasks) != 20 or set(numbered) != set(range(1, 21)) or any(
            numbered[n].max_score != ([1] * 13 + [2, 3, 2, 2, 3, 4, 4])[n-1]
            or numbered[n].part != ('test' if n <= 13 else 'second')
            for n in range(1, 21)
        ):
            continue
        for task in tasks:
            task.name = f'__math_2027_draft_{task.id}'
        s.flush()
        for old, new in mapping.items():
            task = numbered[old]
            task.name = f'№{new}'
            task.part = 'test' if new <= 12 else 'second'
        for old in (6, 13, 17):
            task = numbered[old]
            task.name = f'Проект 2027 · №{old}'
            task.part = 'legacy'
            for membership in s.scalars(select(GroupTask).join(TaskGroup).where(
                    GroupTask.task_id == task.id, TaskGroup.kind.is_not(None))):
                s.delete(membership)
        for new in (12, 16):
            s.add(Task(subject_id=subject.id, name=f'№{new}',
                       part='test' if new <= 12 else 'second',
                       max_score=CATALOG['math']['scores'][new-1]))
        s.flush()
        for kind in ('test', 'second'):
            group = s.scalar(select(TaskGroup).where(TaskGroup.subject_id == subject.id, TaskGroup.kind == kind))
            if group:
                for task in s.scalars(select(Task).where(Task.subject_id == subject.id, Task.name.in_(['№12', '№16']))):
                    if task.part == kind:
                        s.add(GroupTask(group_id=group.id, task_id=task.id))
