def test_pwa_resources_are_public_and_worker_has_root_scope(db):
    from study.config import Settings
    from study.web import create_app
    app = create_app(Settings(_env_file=None, secret_key='pwa-test-secret-long-enough', database_url=str(db.engine.url)))
    client = app.test_client()
    manifest = client.get('/static/manifest.webmanifest')
    assert manifest.status_code == 200
    data = manifest.json
    assert data['start_url'] == '/goals' and data['scope'] == '/' and data['display'] == 'standalone'
    for icon in data['icons']:
        response = client.get(icon['src'])
        assert response.status_code == 200 and response.data.startswith(b'\x89PNG')
    worker = client.get('/sw.js')
    assert worker.status_code == 200 and 'javascript' in worker.content_type
    assert worker.headers['Cache-Control'] == 'no-store'
    assert client.get('/static/offline.html').status_code == 200
    assert b'rel="manifest"' in client.get('/login').data
