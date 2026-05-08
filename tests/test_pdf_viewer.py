import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


def test_preview_endpoint_without_pdf(client):
    """Test preview endpoint returns error without PDF parameter."""
    response = client.get('/preview')
    assert response.status_code == 400
    data = response.get_json()
    assert 'error' in data
    assert 'Missing PDF parameter' in data['error']


def test_preview_endpoint_with_invalid_pdf(client):
    """Test preview endpoint returns error for non-existent PDF."""
    response = client.get('/preview?pdf=nonexistent.pdf')
    assert response.status_code == 404
    data = response.get_json()
    assert 'error' in data
    assert 'PDF not found' in data['error']
