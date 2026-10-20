"""Disposable loopback server used only by browser smoke tests."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from study.web import create_app
from study.config import Settings
from study.models import User, Subject, Task
from study.catalog import ensure_defaults
from study.services import record_activity
from datetime import date


if __name__ == '__main__':
    with TemporaryDirectory(prefix='study-browser-') as directory:
        app = create_app(Settings(_env_file=None, environment='development',
            database_url=f'sqlite:///{directory}/browser.db',
            secret_key='browser-only-test-secret-123456789', debug=True,
            bot_username='', bot_token='', cookie_secure=False,
            base_url='http://127.0.0.1:18081'))
        with app.extensions['db'].session.begin() as s:
            s.add(User(id=1, telegram_id=100, name='Browser smoke', is_teacher=True))
            s.add(User(id=2, telegram_id=200, name='Ученик с длинным именем', teacher_id=1))
            s.flush(); ensure_defaults(s,1)
            subject=Subject(student_id=1,name='Проверка большой истории');s.add(subject);s.flush()
            tasks=[Task(subject_id=subject.id,name=f'Задание {n+1}',part='none',max_score=22) for n in range(12)]
            s.add_all(tasks);s.flush()
            values={task.id:22 for task in tasks}
            for n in range(55):
                record_activity(s,1,1,subject.id,date.today(),'exam' if n==54 else 'practice',values,f'browser-history-{n}')
            tasks[0].max_score=1  # Editors must use historical maxima, not today's limits.
        app.run(host='127.0.0.1', port=18081, use_reloader=False)
