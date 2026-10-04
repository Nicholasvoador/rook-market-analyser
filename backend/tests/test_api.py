"""HTTP layer: validation, secret masking, wallet privacy. TestClient without lifespan, so no background loops run."""
import pytest
from fastapi.testclient import TestClient

from rookery import main
from rookery.config import SETTINGS_FILE

W = "86xCnPeV69n6t3DnyGvkKobf9FdN2H9oiVDdaMpo2MMY"


@pytest.fixture
def api(no_network):
    return TestClient(main.app)  # not used as a context manager -> lifespan (loops) never starts


def test_status_and_branding(api):
    r = api.get("/api/status").json()
    assert r["app"] == "Rook Market Analyser"


def test_secrets_are_write_only_over_http(api):
    assert api.put("/api/secrets", json={"ELFA_API_KEY": "elfak_xyz_secret"}).status_code == 200
    s = api.get("/api/settings").json()
    assert s["secrets"]["ELFA_API_KEY"]["set"] is True
    assert "elfak_xyz_secret" not in api.get("/api/settings").text
    assert api.put("/api/secrets", json={"NOT_A_KEY": "x"}).status_code == 400
    assert api.put("/api/secrets", json={"ELFA_API_KEY": "a\nHERMES_API_URL=http://evil"}).status_code == 400


def test_settings_roundtrip_and_ui_scale(api):
    r = api.put("/api/settings", json={"display": {"ui_scale": 1.4, "high_contrast": True}})
    assert r.status_code == 200
    d = api.get("/api/settings").json()["settings"]["display"]
    assert d["ui_scale"] == 1.4 and d["high_contrast"] is True


def test_wallet_validation_and_masking(api):
    assert api.post("/api/wallets", json={"address": "0xdeadbeef"}).status_code == 400
    r = api.post("/api/wallets", json={"address": W, "label": "cold"})
    assert r.status_code == 200
    listed = api.get("/api/wallets").json()
    assert listed[0]["masked"] == "86xC…2MMY" and listed[0]["label"] == "cold"
    assert api.post("/api/wallets", json={"address": W}).status_code == 400  # duplicate
    assert W not in (SETTINGS_FILE.read_text() if SETTINGS_FILE.exists() else "")
    wid = listed[0]["id"]
    assert api.put(f"/api/wallets/{wid}", json={"include": False}).status_code == 200
    assert api.get("/api/wallets").json()[0]["include"] is False
    assert api.delete(f"/api/wallets/{wid}").status_code == 200
    assert api.get("/api/wallets").json() == []


def test_dex_rejects_bad_mint(api):
    assert api.get("/api/dex/token?mint=nope").status_code == 400
    assert api.get("/api/dex/prices?mints=nope,alsonope").status_code == 400


def test_dex_watchlist_add_remove(api):
    mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    assert api.post("/api/watchlist", json={"kind": "dex", "mint": "nope"}).status_code == 400
    assert api.post("/api/watchlist", json={"kind": "dex", "mint": mint, "sym": "BONK", "name": "Bonk"}).status_code == 200
    api.post("/api/watchlist", json={"kind": "dex", "mint": mint, "sym": "BONK"})  # idempotent
    dex = api.get("/api/settings").json()["settings"]["watchlist"]["dex"]
    assert dex == [{"chain": "solana", "mint": mint, "sym": "BONK", "name": "Bonk"}]
    assert api.delete(f"/api/watchlist?kind=dex&sym={mint}").status_code == 200
    assert api.get("/api/settings").json()["settings"]["watchlist"]["dex"] == []


def test_signal_catalog_lists_all_setups(api):
    cat = api.get("/api/signals/catalog").json()
    assert len(cat) == 13 and all({"label", "dir", "explain"} <= set(c) for c in cat.values())


def test_elfa_status_without_key(api):
    r = api.get("/api/elfa/status").json()
    assert r["configured"] is False and r["chat"]["available"] is False
