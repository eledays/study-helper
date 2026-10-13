from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from .models import Base

class Database:
    def __init__(self, url):
        if url.startswith('sqlite:///') and ':memory:' not in url:
            Path(url.removeprefix('sqlite:///')).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, connect_args={'check_same_thread': False, 'timeout': 20} if url.startswith('sqlite') else {})
        if url.startswith('sqlite'):
            @event.listens_for(self.engine, 'connect')
            def configure(conn, _):
                conn.execute('PRAGMA foreign_keys=ON')
                conn.execute('PRAGMA journal_mode=WAL')
        self.session = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
