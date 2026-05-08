import pytest
import sys
import os
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


def test_upload_no_file(client):
    """Test upload with no file provided."""
    response = client.post('/upload')
    assert response.status_code == 400
    data = response.get_json()
    assert 'error' in data


def test_upload_empty_filename(client):
    """Test upload with empty filename."""
    data = {'file': (BytesIO(b''), '')}
    response = client.post('/upload', data=data)
    assert response.status_code == 400
    data = response.get_json()
    assert 'error' in data


def test_upload_invalid_file_type(client):
    """Test upload rejects non-PDF files."""
    data = {'file': (BytesIO(b'not a pdf'), 'test.txt')}
    response = client.post('/upload', data=data)
    assert response.status_code == 400
    data = response.get_json()
    assert 'PDF' in data['error'] or 'pdf' in data['error'].lower()


def test_paginate_invalid_json(client):
    """Test paginate with invalid JSON."""
    response = client.post(
        '/paginate', data='not json', content_type='application/json'
    )
    assert response.status_code == 400
    data = response.get_json()
    assert 'error' in data


def test_paginate_missing_cut_points(client):
    """Test paginate with missing cut_points."""
    response = client.post(
        '/paginate', json={}, content_type='application/json'
    )
    assert response.status_code in [400, 500]
