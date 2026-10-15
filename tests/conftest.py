import pytest
from study.db import Database
from study.models import User, Subject, Task
from study.config import Settings
from study.web import create_app

@pytest.fixture
def db(tmp_path):
    database = Database(f'sqlite:///{tmp_path}/test.db')
    with database.session.begin() as s:
        s.add_all([User(id=1, telegram_id=100, name='Саша'), User(id=2, telegram_id=200, name='Учитель'),
                   User(id=3, telegram_id=300, name='Другой')])
        s.flush()
        s.get(User, 1).teacher_id = 2
        s.add(Subject(id=1, student_id=1, name='Математика', scale={'3': 60, '0': 0}))
        s.flush()
        s.add_all([Task(id=1, subject_id=1, name='№1', part='test', max_score=1),
                   Task(id=2, subject_id=1, name='№2', part='test', max_score=2),
                   Task(id=3, subject_id=1, name='№19', part='second', max_score=4)])
    return database

@pytest.fixture
def client(db):
    app = create_app(Settings(secret_key='test-secret-that-is-long-enough', database_url=str(db.engine.url)))
    app.config['TESTING'] = True
    client = app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['csrf'] = 'test-csrf'
    return client
