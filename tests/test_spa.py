from sqlalchemy import select
from study.models import Goal

HEADERS = {'X-Study-Navigation': '1'}


def test_spa_views_are_authorized_fragments_with_private_cache_headers(client):
    for section in ('goals', 'schedule', 'analytics', 'editing', 'settings'):
        response = client.get('/'+section, headers=HEADERS)
        assert response.status_code == 200 and response.is_json
        data = response.json
        assert data['user_id'] == 1 and len(data['version']) == 16
        assert data['url'] == '/'+section
        assert '<header>' in data['html'] and '<main' in data['html']
        assert '<html' not in data['html'] and '<head>' not in data['html']
        assert '<script src=' not in data['html']
        assert response.headers['Cache-Control'] == 'no-store'
        assert 'X-Study-Navigation' in response.headers['Vary']
    assert '<!doctype html>' in client.get('/goals').text


def test_prefetch_does_not_consume_flash_messages(client):
    with client.session_transaction() as state:
        state['_flashes'] = [('message', 'Изменения сохранены')]
    prefetch = client.get('/settings', headers={**HEADERS, 'X-Study-Prefetch': '1'})
    assert 'Изменения сохранены' not in prefetch.json['html']
    with client.session_transaction() as state:
        assert state['_flashes'] == [('message', 'Изменения сохранены')]
    active = client.get('/settings', headers=HEADERS)
    assert 'Изменения сохранены' in active.json['html']
    assert 'Изменения сохранены' not in client.get('/settings', headers=HEADERS).json['html']


def test_spa_has_the_same_student_access_checks(client):
    assert client.get('/goals?student=3', headers=HEADERS).status_code == 403
    with client.session_transaction() as state:
        state['user_id'] = 2
    response = client.get('/schedule?student=1', headers=HEADERS)
    assert response.json['user_id'] == 2
    assert '<option value="1" selected>' in response.json['html']
    assert client.get('/schedule?student=3', headers=HEADERS).status_code == 403
    with client.session_transaction() as state:
        state.clear()
    response = client.get('/schedule', headers=HEADERS, follow_redirects=True)
    assert response.status_code == 200 and not response.is_json
    assert '<main' in response.text and '<header>' not in response.text


def test_spa_post_preserves_csrf_validation_and_returns_updated_view(client, db):
    data = {'csrf': 'test-csrf', 'return_section': 'goals', 'subject_id': 1,
            'kind': 'tasks', 'task_ids': [1], 'target': 5, 'weeks': 1, 'week': '2026-10-19'}
    assert client.post('/goals', data={**data, 'csrf': 'wrong'}, headers=HEADERS).status_code == 400
    response = client.post('/goals', data=data, headers=HEADERS, follow_redirects=True)
    assert response.status_code == 200 and response.json['url'].startswith('/goals')
    assert 'goal-card' in response.json['html']
    with db.session() as session:
        assert len(list(session.scalars(select(Goal)))) == 1
