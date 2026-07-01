def test_privacy_page(client):
    r = client.get("/privacy")
    assert r.status_code == 200
    body = r.text.lower()
    assert "message and data rates may apply" in body
    assert "do not sell, rent, or share" in body
    assert "message frequency" in body


def test_terms_page(client):
    r = client.get("/terms")
    assert r.status_code == 200
    assert "STOP" in r.text and "HELP" in r.text
