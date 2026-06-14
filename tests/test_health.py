from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "db_connected" in data


def test_models_registered() -> None:
    from app.db.base import Base

    tables = set(Base.metadata.tables)
    expected = {
        "users",
        "products",
        "product_units",
        "return_events",
        "condition_passports",
        "reverse_wishlist",
        "rescue_listings",
        "pair_rescue_matches",
        "p2p_listings",
        "cart_items",
        "lifeledger_events",
        "warranty_records",
        "green_credit_ledger",
        "impact_events",
    }
    assert expected.issubset(tables)
