"""Persistent local preview, isolated from both application data and browser tests."""
from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from study.catalog import ensure_defaults
from study.config import Settings
from study.groups import ensure_groups
from study.models import Goal, GoalSelection, Subject, Task, TaskGroup, User
from study.services import monday, record_activity, record_exam_total
from study.web import create_app

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / 'data' / 'demo.db'


def seed_demo(session, today):
    for user_id, name in ((1, 'Алексей'), (2, 'Маша')):
        if session.get(User, user_id) is None:
            session.add(User(id=user_id, telegram_id=100 * user_id, name=name,
                             is_teacher=user_id == 1, timezone='Europe/Moscow'))
            session.flush()
        if user_id == 2:
            session.get(User, user_id).teacher_id = 1
        ensure_defaults(session, user_id)
        subjects = list(session.scalars(select(Subject).where(Subject.student_id == user_id).order_by(Subject.id)))
        russian = next(subject for subject in subjects if 'Русский' in subject.name)
        math = next(subject for subject in subjects if 'Математика' in subject.name)
        for subject, kind, target in ((russian, 'test', 8), (math, 'test', 2), (math, 'second', 3)):
            series = f'demo:{user_id}:{subject.id}:{kind}'
            goal = session.scalar(select(Goal).where(Goal.student_id == user_id, Goal.week == monday(today), Goal.series == series))
            if goal is None:
                goal = Goal(student_id=user_id, subject_id=subject.id, kind='group',
                            week=monday(today), target=target, series=series)
                session.add(goal)
                session.flush()
            goal.kind = 'group'
            ensure_groups(session, subject.id)
            group = session.scalar(select(TaskGroup).where(TaskGroup.subject_id == subject.id, TaskGroup.kind == kind))
            if session.get(GoalSelection, goal.id) is None:
                session.add(GoalSelection(goal_id=goal.id, group_id=group.id, task_ids=[], metric='tasks'))
        for subject, count in ((russian, 3), (math, 2)):
            tasks = list(session.scalars(select(Task).where(Task.subject_id == subject.id, Task.part == 'test').order_by(Task.id).limit(count)))
            record_activity(session, user_id, user_id, subject.id, today, 'practice',
                            {task.id: task.max_score for task in tasks},
                            f'demo:{user_id}:{today}:{subject.id}:practice')
        maximum = sum(session.scalars(select(Task.max_score).where(Task.subject_id == russian.id)))
        record_exam_total(session, user_id, user_id, russian.id, today, round(maximum * .7),
                          f'demo:{user_id}:{today}:exam', 'Демонстрационный пробник')
    session.flush()


def create_demo():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    app = create_app(Settings(_env_file=None, environment='development',
        database_url=f'sqlite:///{DATABASE_PATH}', secret_key='local-demo-only-secret-2026-study-helper',
        debug=True, bot_token='', bot_username='', cookie_secure=False,
        base_url='http://127.0.0.1:18082'))
    with app.extensions['db'].session.begin() as session:
        seed_demo(session, datetime.now(ZoneInfo('Europe/Moscow')).date())
    return app


if __name__ == '__main__':
    create_demo().run(host='127.0.0.1', port=18082, use_reloader=False)
