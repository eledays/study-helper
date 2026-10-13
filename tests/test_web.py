import hashlib, hmac, time
import pytest
from study.config import Settings
from study.web import create_app, verify_telegram

@pytest.fixture
def client(db):
    app = create_app(Settings(secret_key='test-secret-that-is-long-enough', database_url=str(db.engine.url)))
    app.config['TESTING'] = True
    client = app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['csrf'] = 'test-csrf'
    return client

def test_pages(client):
    for page in ('plan', 'settings', 'analytics'):
        response = client.get('/'+page)
        assert response.status_code == 200
        assert 'Рядом'.encode() in response.data

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
