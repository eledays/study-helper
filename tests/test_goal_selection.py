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
        assert [r['done'] for r in rows]==[1,3]
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
