from datetime import date
from sqlalchemy import select
from study.models import Goal, GoalSelection, Subject, Task
from study.groups import save_group
from study.services import record_activity, progress

DAY=date(2026,10,12)


def test_custom_group_goal_and_task_selection_repeat(client,db):
    with db.session.begin() as s:
        group=save_group(s,1,'Повторение',[1,3]);s.flush();group_id=group.id
    base={'csrf':'test-csrf','subject_id':1,'target':4,'weeks':2,'week':DAY.isoformat()}
    assert client.post('/goals',data={**base,'kind':'group','group_id':group_id,'metric':'sets'}).status_code==302
    assert client.post('/goals',data={**base,'kind':'tasks','task_ids':[1,3,1]}).status_code==302
    with db.session.begin() as s:
        goals=list(s.scalars(select(Goal).order_by(Goal.id)))
        assert len(goals)==4
        assert [g.kind for g in goals]==['group','group','tasks','tasks']
        assert all(s.get(GoalSelection,g.id).group_id==group_id for g in goals[:2])
        assert all(s.get(GoalSelection,g.id).task_ids==[1,3] for g in goals[2:])
        record_activity(s,1,1,1,DAY,'practice',{1:1,3:0},'whole')
        record_activity(s,1,1,1,DAY,'practice',{1:0},'partial')
        record_activity(s,1,1,1,DAY,'exam',{1:1,3:4},'exam')
        rows=progress(s,1,DAY)
        assert [r['done'] for r in rows]==[2,5]
        assert rows[0]['label']=='Математика. Повторение'
        assert rows[1]['label']=='Математика. №1, №19'
        selection_id=goals[2].id
    assert client.post(f'/delete/goal/{selection_id}',data={'csrf':'test-csrf'}).status_code==302
    with db.session() as s:assert s.get(GoalSelection,selection_id) is None


def test_group_metric_counts_tasks_without_preset_kind(client,db):
    with db.session.begin() as s:
        group=save_group(s,1,'Мои номера',[1,3]);s.flush();group_id=group.id
        record_activity(s,1,1,1,DAY,'practice',{1:0,3:1},'attempts')
    client.post('/goals',data={'csrf':'test-csrf','subject_id':1,'kind':'group','group_id':group_id,
                              'metric':'tasks','target':5,'weeks':1,'week':DAY.isoformat()})
    with db.session() as s:
        assert progress(s,1,DAY)[0]['done']==2
    html=client.get('/goals?week=2026-10-12').data.decode()
    assert 'Мои номера' in html
    assert '<option value="test">' not in html and '<option value="second">' not in html


def test_goal_rejects_foreign_or_empty_selection(client,db):
    with db.session.begin() as s:
        s.add(Subject(id=5,student_id=3,name='Другой предмет'));s.flush()
        s.add(Task(id=5,subject_id=5,name='№1',part='none',max_score=1));s.flush()
        group=save_group(s,5,'Чужая группа',[5]);s.flush();group_id=group.id
    base={'csrf':'test-csrf','subject_id':1,'target':3,'weeks':1,'week':DAY.isoformat()}
    for selection in [{'kind':'group','group_id':group_id},{'kind':'group'},
                      {'kind':'tasks','task_ids':[1,5]},{'kind':'tasks'}]:
        assert client.post('/goals',data={**base,**selection}).status_code==302
    with db.session() as s:assert not list(s.scalars(select(Goal)))


def test_full_variant_goal_defaults_to_sets_and_can_explicitly_count_tasks(client, db):
    from study.groups import ensure_groups
    from study.models import TaskGroup
    with db.session.begin() as s:
        ensure_groups(s, 1)
        group = s.scalar(select(TaskGroup).where(TaskGroup.subject_id == 1, TaskGroup.kind == 'full'))
        group_id = group.id
        record_activity(s, 1, 1, 1, DAY, 'practice', {1: 1, 2: 2, 3: 0}, 'full')
    base = {'csrf': 'test-csrf', 'subject_id': 1, 'kind': 'group', 'group_id': group_id,
            'target': 5, 'weeks': 1, 'week': DAY.isoformat()}
    client.post('/goals', data=base)
    client.post('/goals', data={**base, 'metric': 'tasks'})
    with db.session() as s:
        rows = progress(s, 1, DAY)
        assert [(r['done'], r['unit']) for r in rows] == [(1, 'вариантов'), (3, 'заданий')]
    html = client.get('/?week=2026-10-12').text
    assert 'из 5 вариантов' in html and 'из 5 заданий' in html


def test_goal_counts_week_boundaries_subjects_students_and_attempts(db):
    from datetime import timedelta
    from study.groups import ensure_groups
    from study.models import TaskGroup
    from study.services import record_exam_total
    with db.session.begin() as s:
        ensure_groups(s, 1)
        full = s.scalar(select(TaskGroup).where(TaskGroup.subject_id == 1, TaskGroup.kind == 'full'))
        for metric in ('sets', 'tasks'):
            goal = Goal(student_id=1, subject_id=1, kind='group', week=DAY, target=5, series=metric)
            s.add(goal); s.flush()
            s.add(GoalSelection(goal_id=goal.id, group_id=full.id, metric=metric))
        # Previous Sunday and next Monday belong to other weeks.
        for offset in (-1, 7):
            record_activity(s, 1, 1, 1, DAY+timedelta(days=offset), 'exam',
                            {1: 1, 2: 2, 3: 4}, f'outside-{offset}')
        for offset in (0, 6):
            record_activity(s, 1, 1, 1, DAY+timedelta(days=offset), 'exam',
                            {1: 0, 2: None, 3: 1}, f'inside-{offset}')
        record_exam_total(s, 1, 1, 1, DAY, 3, 'total')
        record_activity(s, 1, 1, 1, DAY, 'practice', {1: 0}, 'partial')
        s.add_all([Subject(id=5, student_id=3, name='Другой ученик'),
                   Subject(id=6, student_id=1, name='Другой предмет')])
        s.flush()
        s.add_all([Task(id=5, subject_id=5, name='№1', part='none', max_score=1),
                   Task(id=6, subject_id=6, name='№1', part='none', max_score=1)])
        s.flush()
        record_activity(s, 3, 3, 5, DAY, 'practice', {5: 1}, 'other-student')
        record_activity(s, 1, 1, 6, DAY, 'practice', {6: 1}, 'other-subject')
        assert [r['done'] for r in progress(s, 1, DAY)] == [3, 5]
        assert [r['done'] for r in progress(s, 1, DAY+timedelta(days=6))] == [3, 5]
