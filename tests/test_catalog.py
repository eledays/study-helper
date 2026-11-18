import pytest
from sqlalchemy import select, func
from study.catalog import CATALOG, add_subject, ensure_defaults
from study.models import User, Subject, Task

@pytest.mark.parametrize('key,count,maximum', [
    ('russian',27,50), ('math',19,32), ('physics',26,45), ('chemistry',34,56),
    ('informatics',27,29), ('biology',28,57), ('history',22,43), ('social',25,58),
])
def test_specification_totals(db,key,count,maximum):
    with db.session.begin() as s:
        subject,created=add_subject(s,3,key)
        s.flush()
        tasks=list(s.scalars(select(Task).where(Task.subject_id==subject.id).order_by(Task.id)))
        assert created and len(tasks)==count
        assert sum(t.max_score for t in tasks)==maximum
        assert sum(t.part=='test' for t in tasks)==CATALOG[key]['first']
        assert subject.scale=={}  # Do not invent an unpublished 2027 conversion
        same,created=add_subject(s,3,key)
        assert same.id==subject.id and not created
        assert s.scalar(select(func.count(Task.id)).where(Task.subject_id==subject.id))==count

def test_defaults_do_not_overwrite_custom_subjects(db):
    with db.session.begin() as s:
        ensure_defaults(s,1)
        assert len(list(s.scalars(select(Subject).where(Subject.student_id==1))))==1
        ensure_defaults(s,3)
        ensure_defaults(s,3)
        assert len(list(s.scalars(select(Subject).where(Subject.student_id==3))))==2
        assert s.get(User,3).share_wellbeing is True
        biology,_=add_subject(s,3,'biology')
        s.flush()
        tasks={t.name:t.max_score for t in s.scalars(select(Task).where(Task.subject_id==biology.id))}
        assert tasks['№21']==1 and tasks['№26']==4


def test_project_math_migration_keeps_history_and_plan(tmp_path):
    from datetime import date
    from study.db import Database
    from study.models import Activity, Goal, GoalSelection, GroupTask, Score, Slot, TaskGroup
    from study.groups import ensure_groups, group_tasks

    url = f'sqlite:///{tmp_path}/project.db'
    database = Database(url)
    with database.session.begin() as s:
        s.add(User(id=1, telegram_id=101))
        s.flush()
        subject = Subject(student_id=1, name=CATALOG['math']['label'], scale={})
        s.add(subject)
        s.flush()
        old_scores = [1] * 13 + [2, 3, 2, 2, 3, 4, 4]
        for n, maximum in enumerate(old_scores, 1):
            s.add(Task(subject_id=subject.id, name=f'№{n}',
                       part='test' if n <= 13 else 'second', max_score=maximum))
        s.flush()
        old = {int(t.name[1:]): t.id for t in s.scalars(select(Task).where(Task.subject_id == subject.id))}
        ensure_groups(s, subject.id)
        group = TaskGroup(subject_id=subject.id, name='Моя группа')
        s.add(group)
        s.flush()
        s.add_all([GroupTask(group_id=group.id, task_id=old[6]), GroupTask(group_id=group.id, task_id=old[20])])
        activity = Activity(student_id=1, subject_id=subject.id, day=date(2026, 11, 1),
                            kind='practice', request_key='old-result')
        s.add(activity)
        s.flush()
        s.add_all([Score(activity_id=activity.id, task_id=old[6], score=1, maximum=1),
                   Score(activity_id=activity.id, task_id=old[20], score=3, maximum=4)])
        goal = Goal(student_id=1, subject_id=subject.id, kind='tasks',
                    week=date(2026, 11, 2), target=2, series='old-goal')
        s.add(goal)
        s.flush()
        s.add(GoalSelection(goal_id=goal.id, task_ids=[old[6], old[20]]))
        s.add(Slot(student_id=1, subject_id=subject.id, day=date(2026, 11, 2),
                   start='15:00', end='16:00', title='Подготовка', task_ids=[old[20]], series='old-slot'))
        subject_id = subject.id

    Database(url)  # startup migration
    Database(url)  # idempotent restart
    with database.session() as s:
        tasks = list(s.scalars(select(Task).where(Task.subject_id == subject_id)))
        active = {t.name: t for t in tasks if t.part != 'legacy'}
        assert len(active) == 19 and len(tasks) == 22
        assert set(active) == {f'№{n}' for n in range(1, 20)}
        assert sum(t.max_score for t in active.values()) == 32
        assert active['№19'].id == old[20]
        assert active['№6'].id == old[7]
        assert active['№13'].id == old[14]
        assert {t.name for t in tasks if t.part == 'legacy'} == {
            'Проект 2027 · №6', 'Проект 2027 · №13', 'Проект 2027 · №17'}
        assert {score.task_id for score in s.scalars(select(Score))} == {old[6], old[20]}
        assert s.scalar(select(GoalSelection)).task_ids == [old[6], old[20]]
        assert s.scalar(select(Slot)).task_ids == [old[20]]
        full = s.scalar(select(TaskGroup).where(TaskGroup.subject_id == subject_id, TaskGroup.kind == 'full'))
        assert len(group_tasks(s, full.id)) == 19
        assert {t.id for t in group_tasks(s, group.id)} == {old[6], old[20]}
    from study.services import record_activity, analytics
    with database.session.begin() as s:
        active_ids = [t.id for t in s.scalars(select(Task).where(
            Task.subject_id == subject_id, Task.part != 'legacy'))]
        result = record_activity(s, 1, 1, subject_id, date(2026, 11, 1), 'exam',
                                 {task_id: None for task_id in active_ids}, 'new-exam')
        result_id = result.id
    with database.session() as s:
        assert len(list(s.scalars(select(Score).where(Score.activity_id == result_id)))) == 19
        history = analytics(s, 1, 1, start=date(2026, 11, 1), end=date(2026, 11, 1))
        assert any(row['task_name'] == 'Проект 2027 · №6' for row in history['tasks'])
