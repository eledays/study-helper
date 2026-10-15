import pytest
from sqlalchemy import select
from study.models import Task, TaskGroup, GroupTask, Subject
from study.groups import ensure_groups, group_tasks, save_group
from study.db import Database


def test_groups_overlap_and_survive_restart(db):
    with db.session.begin() as s:
        ensure_groups(s,1)
        group=save_group(s,1,'Повторение',[1,3])
        s.flush()
        group_id=group.id
        test=s.scalar(select(TaskGroup).where(TaskGroup.subject_id==1,TaskGroup.kind=='test'))
        assert {t.id for t in group_tasks(s,test.id)}=={1,2}
        assert {t.id for t in group_tasks(s,group_id)}=={1,3}
        save_group(s,1,'Повторение',[1,2],group)
        s.flush()
        assert {t.id for t in group_tasks(s,group_id)}=={1,2}
    reopened=Database(str(db.engine.url))
    with reopened.session() as s:
        assert {t.id for t in group_tasks(s,group_id)}=={1,2}
        assert s.get(Task,1).name=='№1'
        assert len(list(s.scalars(select(TaskGroup).where(TaskGroup.subject_id==1))))==3


def test_group_subject_validation(db):
    with db.session.begin() as s:
        s.add(Subject(id=5,student_id=3,name='Другой предмет'))
        s.flush()
        s.add(Task(id=5,subject_id=5,name='№1',part='none',max_score=1))
        s.flush()
        with pytest.raises(ValueError):save_group(s,1,'Неверная группа',[5])
