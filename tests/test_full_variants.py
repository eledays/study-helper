from datetime import date
import pytest
from sqlalchemy import select
from study.models import Activity, Goal, GoalSelection, Task, TaskGroup
from study.groups import ensure_groups, group_order, group_tasks, save_group, attach_standard
from study.services import record_activity, record_exam_total, progress, normalize_full_variants, completed_work, analytics
from study.account import remove_catalog_item

DAY = date(2026, 10, 12)


def test_full_group_is_first_dynamic_and_protected(db):
    with db.session.begin() as s:
        ensure_groups(s, 1)
        groups = list(s.scalars(select(TaskGroup).where(TaskGroup.subject_id == 1).order_by(*group_order())))
        full = groups[0]
        assert full.kind == 'full' and full.name == 'Полный вариант'
        assert {t.id for t in group_tasks(s, full.id)} == {1, 2, 3}
        task = Task(subject_id=1, name='Дополнительное', part='none', max_score=2)
        s.add(task); s.flush(); attach_standard(s, task)
        assert task.id in {t.id for t in group_tasks(s, full.id)}
        with pytest.raises(ValueError): save_group(s, 1, 'Другое', [1], full)
        with pytest.raises(ValueError): remove_catalog_item(s, 1, TaskGroup, full)
        ensure_groups(s, 1)
        assert len(list(s.scalars(select(TaskGroup).where(TaskGroup.subject_id == 1, TaskGroup.kind == 'full')))) == 1


def test_full_practice_and_exam_are_equivalent_in_scores_and_goals(db):
    with db.session.begin() as s:
        ensure_groups(s, 1)
        group = s.scalar(select(TaskGroup).where(TaskGroup.subject_id == 1, TaskGroup.kind == 'full'))
        goal = Goal(student_id=1, subject_id=1, kind='group', week=DAY, target=3, series='variants')
        s.add(goal); s.flush()
        s.add(GoalSelection(goal_id=goal.id, group_id=group.id, metric='sets'))
        practice = record_activity(s, 1, 1, 1, DAY, 'practice', {1:1, 2:2, 3:0}, 'complete-practice')
        exam = record_activity(s, 1, 1, 1, DAY, 'exam', {1:1, 2:2, 3:0}, 'complete-exam')
        assert practice.kind == exam.kind == 'exam' and practice.secondary == exam.secondary == 60
        record_exam_total(s, 1, 1, 1, DAY, 3, 'complete-total')
        assert progress(s, 1, DAY)[0]['done'] == 3
        partial = record_activity(s, 1, 1, 1, DAY, 'practice', {1:1}, 'partial')
        assert partial.kind == 'practice'
        assert progress(s, 1, DAY)[0]['done'] == 3


def test_legacy_normalization_is_conservative_idempotent_and_keeps_snapshots(db):
    with db.session.begin() as s:
        full = record_activity(s, 1, 1, 1, DAY, 'practice', {1:1, 2:2, 3:0}, 'old-complete')
        full.kind = 'practice'; full.secondary = None; full.scale_snapshot = {}
        partial = record_activity(s, 1, 1, 1, DAY, 'practice', {1:1}, 'old-partial')
        s.flush()
        assert normalize_full_variants(s, dry_run=True) == [full.id]
        assert full.kind == 'practice'
        assert normalize_full_variants(s) == [full.id]
        assert partial.kind == 'practice' and full.kind == 'exam'
        assert full.scale_snapshot == {} and full.secondary is None
        s.flush()
        assert normalize_full_variants(s) == []
        ensure_groups(s, 1)
        group = s.scalar(select(TaskGroup).where(TaskGroup.subject_id == 1, TaskGroup.kind == 'full'))
        goal = Goal(student_id=1, subject_id=1, kind='group', week=DAY, target=5, series='legacy')
        s.add(goal); s.flush()
        s.add(GoalSelection(goal_id=goal.id, group_id=group.id, metric='sets'))
        s.flush()
        assert progress(s, 1, DAY)[0]['done'] == 1
        home = completed_work(s, 1, 1, DAY, DAY)
        assert [row['id'] for row in home['history']] == [partial.id]
        assert [(row['id'], row['day'], row['primary'], row['maximum'])
                for row in home['exams']] == [(full.id, DAY.isoformat(), 3, 7)]
        report = analytics(s, 1, 1, start=DAY, end=DAY)
        assert [row['id'] for row in report['exams']] == [full.id]
        assert report['exams'][0]['title'] == 'Полный вариант'
        assert report['exams'][0]['secondary'] is None
