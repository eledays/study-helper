from datetime import date, datetime, UTC
from sqlalchemy import ForeignKey, UniqueConstraint, CheckConstraint, JSON, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase): pass

class User(Base):
    __tablename__ = 'users'
    __table_args__ = (Index('ix_users_teacher', 'teacher_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column(default='')
    gender: Mapped[str] = mapped_column(default='neutral')
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    timezone: Mapped[str] = mapped_column(default='Europe/Moscow')
    evening: Mapped[str] = mapped_column(default='21:00')
    reminders: Mapped[bool] = mapped_column(default=True)
    wellbeing: Mapped[bool] = mapped_column(default=True)
    share_wellbeing: Mapped[bool] = mapped_column(default=True)
    bot_active: Mapped[bool] = mapped_column(default=False)

class Subject(Base):
    __tablename__ = 'subjects'
    __table_args__ = (Index('ix_subjects_student', 'student_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    name: Mapped[str]
    scale: Mapped[dict] = mapped_column(JSON, default=dict)

class Task(Base):
    __tablename__ = 'tasks'
    __table_args__ = (UniqueConstraint('subject_id', 'name'), CheckConstraint('max_score > 0'))
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey('subjects.id'))
    name: Mapped[str]
    part: Mapped[str]  # Legacy seed / initial group; memberships live in GroupTask
    max_score: Mapped[int]

class Goal(Base):
    __tablename__ = 'goals'
    __table_args__ = (CheckConstraint('target > 0'), Index('ix_goals_student_week', 'student_id', 'week'))
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    subject_id: Mapped[int] = mapped_column(ForeignKey('subjects.id'))
    task_id: Mapped[int | None] = mapped_column(ForeignKey('tasks.id'))
    kind: Mapped[str]  # group / tasks; test, second, task retained for older goals
    week: Mapped[date]
    target: Mapped[int]
    series: Mapped[str]

class Slot(Base):
    __tablename__ = 'slots'
    __table_args__ = (Index('ix_slots_student_day', 'student_id', 'day'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    day: Mapped[date]
    start: Mapped[str]
    end: Mapped[str]
    title: Mapped[str]
    kind: Mapped[str] = mapped_column(default='study') # study, exam, rest
    subject_id: Mapped[int | None] = mapped_column(ForeignKey('subjects.id'))
    task_ids: Mapped[list] = mapped_column(JSON, default=list)
    series: Mapped[str]

class Activity(Base):
    __tablename__ = 'activities'
    __table_args__ = (Index('ix_activities_student_day', 'student_id', 'day'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    subject_id: Mapped[int] = mapped_column(ForeignKey('subjects.id'))
    day: Mapped[date]
    kind: Mapped[str]  # practice, test, exam
    title: Mapped[str] = mapped_column(default='')
    secondary: Mapped[int | None]
    scale_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    request_key: Mapped[str] = mapped_column(unique=True)

class Score(Base):
    __tablename__ = 'scores'
    __table_args__ = (UniqueConstraint('activity_id', 'task_id'), CheckConstraint('score >= 0 AND score <= maximum'))
    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey('activities.id', ondelete='CASCADE'))
    task_id: Mapped[int] = mapped_column(ForeignKey('tasks.id'))
    score: Mapped[int]
    maximum: Mapped[int]
    attempted: Mapped[bool] = mapped_column(default=True)

class Daily(Base):
    __tablename__ = 'daily'
    __table_args__ = (UniqueConstraint('student_id', 'day'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    day: Mapped[date]
    status: Mapped[str] = mapped_column(default='rest')
    wellbeing_asked: Mapped[bool] = mapped_column(default=False)
    mood: Mapped[int | None]
    load: Mapped[int | None]

class Invite(Base):
    __tablename__ = 'invites'
    token: Mapped[str] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    expires: Mapped[datetime]

class Delivery(Base):
    __tablename__ = 'deliveries'
    key: Mapped[str] = mapped_column(primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC).replace(tzinfo=None))

class Draft(Base):
    __tablename__ = 'drafts'
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)

class TaskGroup(Base):
    __tablename__ = 'task_groups'
    __table_args__ = (UniqueConstraint('subject_id', 'name'), UniqueConstraint('subject_id', 'kind'))
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey('subjects.id'))
    name: Mapped[str]
    kind: Mapped[str | None]  # test / second for standard groups, NULL for custom groups

class GroupTask(Base):
    __tablename__ = 'group_tasks'
    group_id: Mapped[int] = mapped_column(ForeignKey('task_groups.id', ondelete='CASCADE'), primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey('tasks.id', ondelete='CASCADE'), primary_key=True)

class ExamTotal(Base):
    __tablename__ = 'exam_totals'
    __table_args__ = (CheckConstraint('primary_score >= 0 AND primary_score <= maximum'),)
    activity_id: Mapped[int] = mapped_column(ForeignKey('activities.id', ondelete='CASCADE'), primary_key=True)
    primary_score: Mapped[int]
    maximum: Mapped[int]

class UserAvatar(Base):
    __tablename__ = 'user_avatars'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    photo_url: Mapped[str]

class GoalSelection(Base):
    __tablename__ = 'goal_selections'
    goal_id: Mapped[int] = mapped_column(ForeignKey('goals.id', ondelete='CASCADE'), primary_key=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey('task_groups.id'))
    task_ids: Mapped[list] = mapped_column(JSON, default=list)
    metric: Mapped[str] = mapped_column(default='tasks')  # tasks / sets


class BotOutbox(Base):
    __tablename__ = 'bot_outbox'
    __table_args__ = (Index('ix_outbox_chat_id', 'chat_id', 'id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int]
    method: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSON)
    placeholder: Mapped[int | None]
    attempts: Mapped[int] = mapped_column(default=0)
    retry_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC).replace(tzinfo=None))
    failed: Mapped[bool] = mapped_column(default=False)


class BotMessage(Base):
    __tablename__ = 'bot_messages'
    placeholder: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int]
    message_id: Mapped[int]


class UserSetup(Base):
    __tablename__ = 'user_setup'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
