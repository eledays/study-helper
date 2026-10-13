from datetime import date, datetime, UTC
from sqlalchemy import ForeignKey, UniqueConstraint, CheckConstraint, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase): pass

class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column(default='')
    gender: Mapped[str] = mapped_column(default='neutral')
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    timezone: Mapped[str] = mapped_column(default='Europe/Moscow')
    evening: Mapped[str] = mapped_column(default='21:00')
    reminders: Mapped[bool] = mapped_column(default=True)
    wellbeing: Mapped[bool] = mapped_column(default=True)
    share_wellbeing: Mapped[bool] = mapped_column(default=False)
    bot_active: Mapped[bool] = mapped_column(default=False)

class Subject(Base):
    __tablename__ = 'subjects'
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
    part: Mapped[str]
    max_score: Mapped[int]

class Goal(Base):
    __tablename__ = 'goals'
    __table_args__ = (CheckConstraint('target > 0'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    subject_id: Mapped[int] = mapped_column(ForeignKey('subjects.id'))
    task_id: Mapped[int | None] = mapped_column(ForeignKey('tasks.id'))
    kind: Mapped[str]  # test, second, task
    week: Mapped[date]
    target: Mapped[int]
    series: Mapped[str]

class Slot(Base):
    __tablename__ = 'slots'
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
    status: Mapped[str] = mapped_column(default='unknown')
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
