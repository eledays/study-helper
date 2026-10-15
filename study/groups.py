"""Independent tasks with overlapping groups; part is retained for old databases"""
from sqlalchemy import select
from .models import Task, TaskGroup, GroupTask


def ensure_groups(s, subject_id):
    groups = list(s.scalars(select(TaskGroup).where(TaskGroup.subject_id == subject_id)))
    for kind, name in [('test', 'Тестовая часть'), ('second', 'Вторая часть')]:
        if any(g.kind == kind for g in groups):
            continue
        group = TaskGroup(subject_id=subject_id, name=name, kind=kind)
        s.add(group)
        s.flush()
        for task in s.scalars(select(Task).where(Task.subject_id == subject_id, Task.part == kind)):
            s.add(GroupTask(group_id=group.id, task_id=task.id))
    s.flush()


def group_tasks(s, group_id):
    return list(s.scalars(select(Task).join(GroupTask, Task.id == GroupTask.task_id)
                         .where(GroupTask.group_id == group_id).order_by(Task.id)))


def standard_tasks(s, subject_id, kind):
    ensure_groups(s, subject_id)
    group = s.scalar(select(TaskGroup).where(TaskGroup.subject_id == subject_id, TaskGroup.kind == kind))
    return group_tasks(s, group.id)


def attach_standard(s, task):
    if task.part not in ('test', 'second'):
        return
    ensure_groups(s, task.subject_id)
    group = s.scalar(select(TaskGroup).where(TaskGroup.subject_id == task.subject_id, TaskGroup.kind == task.part))
    if not s.get(GroupTask, (group.id, task.id)):
        s.add(GroupTask(group_id=group.id, task_id=task.id))


def save_group(s, subject_id, name, task_ids, group=None):
    name = name.strip()[:80]
    ids = set(map(int, task_ids))
    if not name or not ids:
        raise ValueError('Укажите название и выберите задания')
    valid = set(s.scalars(select(Task.id).where(Task.subject_id == subject_id)))
    if not ids <= valid:
        raise ValueError('Выберите задания этого предмета')
    duplicate = s.scalar(select(TaskGroup).where(TaskGroup.subject_id == subject_id, TaskGroup.name == name))
    if duplicate and (not group or duplicate.id != group.id):
        raise ValueError('Группа с таким названием уже есть')
    if not group:
        group = TaskGroup(subject_id=subject_id, name=name)
        s.add(group)
        s.flush()
    else:
        group.name = name
        for membership in s.scalars(select(GroupTask).where(GroupTask.group_id == group.id)):
            if membership.task_id not in ids:
                s.delete(membership)
    for task_id in ids:
        if not s.get(GroupTask, (group.id, task_id)):
            s.add(GroupTask(group_id=group.id, task_id=task_id))
    return group
