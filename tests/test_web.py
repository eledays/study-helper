import hashlib, hmac, time
from study.config import Settings
from study.web import create_app, verify_telegram

def test_pages(client):
    for page in ('plan', 'settings', 'analytics', 'editing', 'editing?tab=subjects', 'editing?tab=plan'):
        response = client.get('/'+page)
        assert response.status_code == 200
        assert 'study-bot'.encode() in response.data

def test_csrf_and_cross_student(client):
    assert client.post('/subjects', data={'name':'Физика'}).status_code == 400
    assert client.get('/analytics?student=3').status_code == 403
    assert client.post('/subjects', data={'csrf':'test-csrf','student':3,'name':'Физика'}).status_code == 403

def test_teacher_can_plan_not_results(client):
    with client.session_transaction() as session: session['user_id'] = 2
    assert client.get('/plan?student=1').status_code == 200
    assert client.post('/goals', data={'csrf':'test-csrf','student':1,'subject_id':1,'kind':'second',
                                     'target':5,'weeks':4,'week':'2026-10-12'}).status_code == 302
    assert client.post('/activity/1/delete', data={'csrf':'test-csrf','student':1}).status_code == 403

def test_telegram_signature_and_expiry():
    token = 'test-token'; now=int(time.time())
    data = {'id':'123', 'first_name':'Саша', 'auth_date':str(now)}
    data['hash'] = hmac.new(hashlib.sha256(token.encode()).digest(),
        '\n'.join(f'{k}={v}' for k,v in sorted(data.items())).encode(), hashlib.sha256).hexdigest()
    assert verify_telegram(data, token, now)
    assert not verify_telegram(data, token, now+601)
    assert not verify_telegram(data, token, now-1)
    data['id']='999'
    assert not verify_telegram(data, token, now)

def test_catalog_access_and_week_preserved(client, db):
    response=client.post('/subjects/catalog',data={'csrf':'test-csrf','catalog_key':'math','return_week':'2027-02-01'})
    assert response.status_code==302 and 'week=2027-02-01' in response.location
    assert client.post('/subjects/catalog',data={'csrf':'test-csrf','catalog_key':'math','student':3}).status_code==403
    assert client.get('/settings').status_code==200

def test_group_edit_access(client,db):
    from study.models import TaskGroup, GroupTask
    from sqlalchemy import select
    response=client.post('/groups',data={'csrf':'test-csrf','subject_id':1,'name':'Смешанная','task_ids':[1,3]})
    assert response.status_code==302
    with db.session() as s:
        group=s.scalar(select(TaskGroup).where(TaskGroup.name=='Смешанная'))
        group_id=group.id
        assert set(s.scalars(select(GroupTask.task_id).where(GroupTask.group_id==group_id)))=={1,3}
    assert client.post(f'/groups/{group_id}',data={'csrf':'test-csrf','student':3,'name':'Чужая','task_ids':[1]}).status_code==403
    assert client.get('/settings').status_code==200

def test_total_exam_delete_removes_total(client, db):
    from datetime import date
    from study.services import record_exam_total
    from study.models import ExamTotal,Activity
    with db.session.begin() as s:
        activity=record_exam_total(s,1,1,1,date(2026,10,12),3,'total-for-delete')
        activity_id=activity.id
    response=client.get('/editing')
    assert response.status_code==200
    assert f'/activity/{activity_id}/editor'.encode() in response.data
    editor=client.get(f'/activity/{activity_id}/editor')
    assert editor.status_code==200 and 'Первичный балл' in editor.json['html']
    assert client.post(f'/activity/{activity_id}/delete',data={'csrf':'test-csrf'}).status_code==302
    assert client.get(f'/activity/{activity_id}/editor').status_code==404
    with db.session() as s:
        assert s.get(Activity,activity_id) is None
        assert s.get(ExamTotal,activity_id) is None

def test_separate_planning_pages_and_short_labels(client,db):
    from datetime import date
    from study.catalog import add_subject
    from study.models import Goal,Slot
    with db.session.begin() as s:
        subject,_=add_subject(s,1,'russian')
        s.add(Goal(student_id=1,subject_id=subject.id,kind='test',week=date(2026,10,12),target=4,series='ui'))
        s.add(Slot(student_id=1,subject_id=subject.id,day=date(2026,10,12),start='16:00',end='17:00',title='Разбор ошибок',series='ui'))
    goals=client.get('/goals?week=2026-10-12').data.decode()
    schedule=client.get('/schedule?week=2026-10-12').data.decode()
    assert '<h1>' not in goals and '<h1>' not in schedule
    assert 'Русский язык. Тестовая часть' in goals and 'ЕГЭ 2027' not in goals
    assert 'Разбор ошибок' not in goals and 'Разбор ошибок' in schedule
    assert 'goal-card' not in schedule
    assert goals.count('id="board-edit"')==1 and 'Изменить цель' not in goals
    assert 'class="profile-card profile-name"' in goals
    assert '/logout' not in goals and 'class="brand"' not in goals
    assert '/logout' in client.get('/settings').data.decode()
    assert '<progress' not in goals and '--goal-fill:' in goals
    with client.session_transaction() as session:session['user_id']=2
    teacher=client.get('/goals?student=1&week=2026-10-12').data.decode()
    assert 'class="profile-card profile-student"' in teacher
    assert 'Учитель' in teacher and 'Саша' in teacher
    assert 'aria-current="page" href="/goals?student=1&amp;week=2026-10-12"' in teacher


def test_planning_edits_return_to_same_section_and_mode(client,db):
    from datetime import date
    from study.models import Goal,Slot
    with db.session.begin() as s:
        goal=Goal(student_id=1,subject_id=1,kind='test',week=date(2026,10,12),target=4,series='edit')
        slot=Slot(student_id=1,subject_id=1,day=date(2026,10,12),start='16:00',end='17:00',title='Занятие',series='edit')
        s.add_all([goal,slot]);s.flush();goal_id,slot_id=goal.id,slot.id
    base={'csrf':'test-csrf','return_week':'2026-10-12','editing':'1','scope':'one'}
    response=client.post(f'/goals/{goal_id}',data={**base,'target':6})
    assert '/goals?' in response.location and 'edit=1' in response.location and 'week=2026-10-12' in response.location
    assert 'section=editing' not in response.location
    assert client.get('/editing?tab=plan&week=2026-10-12').status_code == 200
    response=client.post(f'/slots/{slot_id}',data={**base,'start':'17:00','end':'18:00'})
    assert '/schedule?' in response.location and 'edit=1' in response.location
    response=client.post(f'/delete/slot/{slot_id}',data=base)
    assert '/schedule?' in response.location

def test_signed_telegram_avatar_is_stored_updated_and_optional(db):
    from study.models import UserAvatar
    from study.db import Database
    token='test-avatar-token'
    app=create_app(Settings(secret_key='test-avatar-secret-long-enough',database_url=str(db.engine.url),bot_token=token))
    client=app.test_client()
    def signed(photo=None):
        data={'id':'100','first_name':'Саша','auth_date':str(int(time.time()))}
        if photo is not None:data['photo_url']=photo
        check='\n'.join(f'{k}={v}' for k,v in sorted(data.items()))
        data['hash']=hmac.new(hashlib.sha256(token.encode()).digest(),check.encode(),hashlib.sha256).hexdigest()
        return data
    def login(data):
        client.get('/login')
        with client.session_transaction() as state:
            data = {**data, 'state': state['login_state']}
        return client.get('/auth/telegram', query_string=data)
    photo='https://cdn4.telesco.pe/file/avatar.jpg'
    assert login(signed(photo)).status_code==302
    reopened=Database(str(db.engine.url))
    with reopened.session() as s:assert s.get(UserAvatar,1).photo_url==photo
    goals=client.get('/goals').data.decode()
    assert f'src="{photo}"' in goals and 'class="profile-avatar"' in goals
    data=signed(photo);data['photo_url']='https://example.com/tampered.jpg'
    assert login(data).status_code==403
    with db.session() as s:assert s.get(UserAvatar,1).photo_url==photo
    assert login(signed('javascript:alert(1)')).status_code==302
    assert 'src="/profile/avatar"' in client.get('/goals').data.decode()
    assert login(signed()).status_code==302
    assert 'src="/profile/avatar"' in client.get('/goals').data.decode()


def test_teacher_role_controls_settings_and_invites(client, db):
    from study.models import User
    page = client.get('/settings').text
    assert 'Мои данные' not in page
    assert 'Учитель и ученики' not in page
    assert client.post('/invite', data={'csrf': 'test-csrf'}).status_code == 403
    with db.session.begin() as s:
        s.get(User, 1).is_teacher = True
    assert 'Учитель и ученики' in client.get('/settings').text
    assert client.post('/invite', data={'csrf': 'test-csrf'}).status_code == 302


def test_login_has_fixed_dark_theme(client):
    page = client.get('/login').text
    assert 'data-theme="dark" data-fixed-theme="dark"' in page
    assert 'id="theme-toggle"' not in page
    assert 'id="theme-toggle"' in client.get('/settings').text


def test_avatar_fallback_is_authenticated_private_and_does_not_expose_token(db, monkeypatch):
    app = create_app(Settings(_env_file=None, secret_key='avatar-fallback-secret-long-enough',
                             database_url=str(db.engine.url), bot_token='123456:test-secret'))
    client = app.test_client()
    assert client.get('/profile/avatar').status_code == 302
    with client.session_transaction() as state: state['user_id'] = 1
    calls = []
    def photo(settings, telegram_id):
        calls.append(telegram_id)
        return b'\xff\xd8\xffphoto', 'image/jpeg'
    monkeypatch.setattr('study.web.fetch_avatar', photo)
    response = client.get('/profile/avatar?student=3')
    assert response.status_code == 403
    response = client.get('/profile/avatar')
    assert response.status_code == 200 and response.content_type == 'image/jpeg'
    assert response.headers['Cache-Control'] == 'no-store' and calls == [100]
    assert b'test-secret' not in response.data
    monkeypatch.setattr('study.web.fetch_avatar', lambda *args: None)
    assert client.get('/profile/avatar').status_code == 404
    def fail(*args): raise TimeoutError()
    monkeypatch.setattr('study.web.fetch_avatar', fail)
    assert client.get('/profile/avatar').status_code == 503


def test_avatar_proxy_failure_returns_unavailable_without_leaking_details(db, monkeypatch, caplog):
    from python_socks import ProxyError
    app = create_app(Settings(_env_file=None, secret_key='avatar-proxy-secret-long-enough',
                             database_url=str(db.engine.url), bot_token='123456:test-secret'))
    client = app.test_client()
    with client.session_transaction() as state: state['user_id'] = 1
    def fail(*args): raise ProxyError('sensitive-proxy-details')
    monkeypatch.setattr('study.web.fetch_avatar', fail)
    assert client.get('/profile/avatar').status_code == 503
    assert 'sensitive-proxy-details' not in caplog.text
