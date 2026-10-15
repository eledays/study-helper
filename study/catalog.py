"""ЕГЭ 2027, проекты ФИПИ, проверены 13 октября 2026.

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
    'math': {'name': 'Математика · профиль', 'first': 13,
             'scores': [1] * 13 + [2, 3, 2, 2, 3, 4, 4], 'code': 'ma'},
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
