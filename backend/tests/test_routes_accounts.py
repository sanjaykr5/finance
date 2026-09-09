def test_create_account_without_password(client):
    r = client.post("/api/accounts", json={"kind": "upi", "provider": "PhonePe"})
    assert r.status_code == 200
    body = r.json()
    assert body["has_password"] is False
    assert "table_name" not in body
    assert "password" not in body


def test_create_account_with_password_not_echoed(client):
    r = client.post(
        "/api/accounts",
        json={"kind": "credit_card", "provider": "HSBC", "password": "s3cr3t"},
    )
    body = r.json()
    assert body["has_password"] is True
    assert "password" not in body
    assert "encrypted_password" not in body


def test_list_accounts_reports_has_password(client):
    client.post("/api/accounts", json={"kind": "bank", "provider": "SBI", "password": "x"})
    client.post("/api/accounts", json={"kind": "upi", "provider": "GPay"})
    r = client.get("/api/accounts")
    by_provider = {a["provider"]: a for a in r.json()}
    assert by_provider["SBI"]["has_password"] is True
    assert by_provider["GPay"]["has_password"] is False


def test_patch_account_updates_nickname_only(client):
    created = client.post(
        "/api/accounts", json={"kind": "bank", "provider": "ICICI", "password": "keep-me"}
    ).json()
    r = client.patch(f"/api/accounts/{created['id']}", json={"nickname": "Joint account"})
    assert r.status_code == 200
    body = r.json()
    assert body["nickname"] == "Joint account"
    assert body["has_password"] is True  # untouched by this PATCH


def test_patch_account_clears_password(client):
    created = client.post(
        "/api/accounts", json={"kind": "bank", "provider": "Axis", "password": "old"}
    ).json()
    r = client.patch(f"/api/accounts/{created['id']}", json={"password": ""})
    assert r.status_code == 200
    assert r.json()["has_password"] is False


def test_patch_unknown_account_404s(client):
    r = client.patch("/api/accounts/999999", json={"nickname": "x"})
    assert r.status_code == 404
