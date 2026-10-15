"""Export and explicit removal of the authenticated user's own data."""
from sqlalchemy import select, delete, update
from .models import (User, Subject, Task, Goal, Slot, Activity, Score, Daily, Invite,
                     Draft, TaskGroup, GroupTask, UserAvatar, GoalSelection, ExamTotal,
                     BotOutbox, BotMessage, Delivery, UserSetup)


def own_queries(user):
    subjects = select(Subject.id).where(Subject.student_id == user.id)
    tasks = select(Task.id).where(Task.subject_id.in_(subjects))
    activities = select(Activity.id).where(Activity.student_id == user.id)
    groups = select(TaskGroup.id).where(TaskGroup.subject_id.in_(subjects))
    goals = select(Goal.id).where(Goal.student_id == user.id)
    return [
        (User, User.id == user.id), (Subject, Subject.id.in_(subjects)),
        (Task, Task.id.in_(tasks)), (TaskGroup, TaskGroup.id.in_(groups)),
        (GroupTask, GroupTask.group_id.in_(groups)), (Goal, Goal.id.in_(goals)),
        (GoalSelection, GoalSelection.goal_id.in_(goals)), (Slot, Slot.student_id == user.id),
        (Activity, Activity.id.in_(activities)), (Score, Score.activity_id.in_(activities)),
        (ExamTotal, ExamTotal.activity_id.in_(activities)), (Daily, Daily.student_id == user.id),
        (Draft, Draft.student_id == user.id), (UserAvatar, UserAvatar.user_id == user.id),
        (UserSetup, UserSetup.user_id == user.id)]


def delete_account(s, user):
    filters = dict(own_queries(user))
    s.execute(update(User).where(User.teacher_id == user.id).values(teacher_id=None))
    for model in (GoalSelection, Score, ExamTotal, GroupTask, Goal, Slot, Activity,
                  Daily, Draft, UserAvatar, UserSetup, TaskGroup, Task, Subject):
        s.execute(delete(model).where(filters[model]), execution_options={'synchronize_session': False})
    s.execute(delete(Invite).where(Invite.teacher_id == user.id))
    s.execute(delete(BotOutbox).where(BotOutbox.chat_id == user.telegram_id))
    s.execute(delete(BotMessage).where(BotMessage.chat_id == user.telegram_id))
    s.execute(delete(Delivery).where(Delivery.key.startswith(f'{user.id}:')))
    s.delete(user)


def remove_catalog_item(s, student_id, model, item):
    if s.get(Draft, student_id):
        raise ValueError('Сначала завершите ввод в боте или выполните /cancel')
    if model is TaskGroup:
        if s.scalar(select(GoalSelection.goal_id).where(GoalSelection.group_id == item.id).limit(1)):
            raise ValueError('Группа используется в целях. Сначала удалите эти цели')
    elif model is Task:
        referenced = s.scalar(select(Score.id).where(Score.task_id == item.id).limit(1))
        referenced = referenced or s.scalar(select(Goal.id).where(Goal.task_id == item.id).limit(1))
        referenced = referenced or any(item.id in row.task_ids for row in s.scalars(select(Slot).where(Slot.student_id == student_id)))
        referenced = referenced or any(item.id in row.task_ids for row in s.scalars(select(GoalSelection).join(Goal).where(Goal.student_id == student_id)))
        referenced = referenced or s.scalar(select(GroupTask.group_id).join(GoalSelection, GoalSelection.group_id == GroupTask.group_id).where(GroupTask.task_id == item.id).limit(1))
        if referenced:
            raise ValueError('Задание используется в истории или плане. Его удаление изменит учёт; сохраните его')
        s.execute(delete(GroupTask).where(GroupTask.task_id == item.id))
    else:
        if s.get(UserSetup, student_id) is None:
            s.add(UserSetup(user_id=student_id))
        if any(s.scalar(select(m.id).where(m.subject_id == item.id).limit(1)) for m in (Activity, Goal, Slot)):
            raise ValueError('Предмет используется в истории или плане. Сначала удалите связанные записи')
        groups = select(TaskGroup.id).where(TaskGroup.subject_id == item.id)
        s.execute(delete(GroupTask).where(GroupTask.group_id.in_(groups)))
        s.execute(delete(TaskGroup).where(TaskGroup.subject_id == item.id))
        s.execute(delete(Task).where(Task.subject_id == item.id))
    s.delete(item)
