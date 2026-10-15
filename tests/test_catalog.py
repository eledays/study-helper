import pytest
from sqlalchemy import select, func
from study.catalog import CATALOG, add_subject, ensure_defaults
from study.models import User, Subject, Task

@pytest.mark.parametrize('key,count,maximum', [
    ('russian',27,50), ('math',20,33), ('physics',26,45), ('chemistry',34,56),
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
