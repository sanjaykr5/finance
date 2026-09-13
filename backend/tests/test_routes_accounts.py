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


def test_patch_account_clears_password(client):
    created = client.post(
        "/api/accounts", json={"kind": "bank", "provider": "Axis", "password": "old"}
    ).json()
    r = client.patch(f"/api/accounts/{created['id']}", json={"password": ""})
    assert r.status_code == 200
    assert r.json()["has_password"] is False


def test_list_accounts_filters_by_kind(client):
    """The Transactions/Credit Cards tabs populate their account dropdown
    from GET /accounts?kind=..., so multi-value kind filtering here needs
    to actually narrow the results the way /transactions's does.
    """
    client.post("/api/accounts", json={"kind": "bank", "provider": "SBI"})
    client.post("/api/accounts", json={"kind": "upi", "provider": "PhonePe"})
    client.post("/api/accounts", json={"kind": "credit_card", "provider": "HSBC"})

    r = client.get("/api/accounts", params=[("kind", "bank"), ("kind", "upi")])
    assert r.status_code == 200
    kinds = {a["kind"] for a in r.json()}
    assert kinds == {"bank", "upi"}


def test_patch_unknown_account_404s(client):
    r = client.patch("/api/accounts/999999", json={"password": "x"})
    assert r.status_code == 404
