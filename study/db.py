from pathlib import Path
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from .models import Base

class Database:
    def __init__(self, url, initialize=True):
        if url.startswith('sqlite:///') and ':memory:' not in url:
            Path(url.removeprefix('sqlite:///')).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, hide_parameters=True,
            connect_args={'check_same_thread': False, 'timeout': 3} if url.startswith('sqlite') else {})
        if url.startswith('sqlite'):
            @event.listens_for(self.engine, 'connect')
            def configure(conn, _):
                conn.execute('PRAGMA foreign_keys=ON')
                conn.execute('PRAGMA journal_mode=WAL')
        self.session = sessionmaker(self.engine, expire_on_commit=False)
        if not initialize:
            try:
                with self.engine.connect() as conn:
                    version = conn.execute(text('SELECT version FROM schema_version WHERE id=1')).scalar_one()
            except SQLAlchemyError:
                raise RuntimeError('База недоступна или схема не инициализирована. Проверьте доступ и выполните python -m study.manage migrate') from None
            if version != 3:
                raise RuntimeError('Неподдерживаемая версия схемы. Выполните python -m study.manage migrate')
            return
        # Add the explicit teacher role to existing databases. Migration is additive.
        inspector = inspect(self.engine)
        if inspector.has_table('schema_version'):
            with self.engine.connect() as conn:
                version = conn.execute(text('SELECT version FROM schema_version WHERE id=1')).scalar_one_or_none()
                if version not in (None, 1, 2, 3):
                    raise RuntimeError('База создана другой версией приложения; откат схемы запрещён')
        if inspector.has_table('users') and 'is_teacher' not in {c['name'] for c in inspector.get_columns('users')}:
            with self.engine.begin() as conn:
                conn.execute(text('ALTER TABLE users ADD COLUMN is_teacher BOOLEAN NOT NULL DEFAULT FALSE'))
        # Version 1 adopts the existing additive schema without changing IDs or
        # historical scores. Refuse unknown column changes instead of guessing.
        inspector = inspect(self.engine)
        for table in Base.metadata.sorted_tables:
            if inspector.has_table(table.name):
                columns = {c['name'] for c in inspector.get_columns(table.name)}
                missing = set(table.columns.keys()) - columns
                if missing:
                    raise RuntimeError(f'Несовместимая схема {table.name}: отсутствуют {sorted(missing)}. Восстановите совместимую версию; требуется отдельная миграция')
        Base.metadata.create_all(self.engine)
        for table in Base.metadata.sorted_tables:
            for index in table.indexes:
                index.create(self.engine, checkfirst=True)
        # Add group tables and seed memberships without changing existing task IDs or scores
        from sqlalchemy import select, update
        from .models import Subject, Daily, Activity
        from .groups import ensure_groups
        with self.session.begin() as s:
            s.execute(update(Daily).where(Daily.status.in_(['missed', 'unknown'])).values(
                status='rest'))
            s.execute(update(Daily).where(Daily.status == 'rest',
                select(Activity.id).where(Activity.student_id == Daily.student_id, Activity.day == Daily.day).exists()
            ).values(status='study'))
            for subject_id in s.scalars(select(Subject.id)):
                ensure_groups(s, subject_id)
        with self.engine.begin() as conn:
            conn.execute(text('CREATE TABLE IF NOT EXISTS schema_version (id INTEGER PRIMARY KEY CHECK (id=1), version INTEGER NOT NULL)'))
            if conn.execute(text('SELECT version FROM schema_version WHERE id=1')).scalar_one_or_none() is None:
                conn.execute(text('INSERT INTO schema_version (id, version) VALUES (1, 3)'))
            conn.execute(text('UPDATE schema_version SET version=3 WHERE id=1'))
