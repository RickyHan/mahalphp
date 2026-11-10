"""업체 문자(뿌리오): 테스트 모드 · 건물×업체 담당자 번호 우선 · 번호 없으면 카톡방 · 실패하면 카톡방으로 대신 · 이력"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import complaints as cp
from app import db, sms, sms_dispatch, store
from app.main import app

from .conftest import ADMIN_PASSWORD, ADMIN_USER, API_KEY

KEY = {"X-Api-Key": API_KEY}
DEVICE = "sms-phone"


class Gem:
    def classify(self, items):
        return [cp.Verdict(i.id, "CCTV" in i.text, "CCTV" if "CCTV" in i.text else "기타", "CCTV 고장", 0.9) for i in items]


class FakePpurio:
    def __init__(self, fail: dict[str, sms.PpurioError] | None = None):
        self.sent: list[tuple[str, str]] = []
        self.fail = fail or {}

    def send(self, *, sender, to, text, ref_key, subject=sms.DEFAULT_SUBJECT):
        if to in self.fail:
            raise self.fail[to]
        self.sent.append((to, text))
        return sms.SendResult(f"k{len(self.sent)}", sms.message_type(text))


@pytest.fixture
def admin(settings):
    c = TestClient(app)
    assert c.post("/admin/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD}, follow_redirects=False).status_code == 303
    return c


def _post(room, sender, text):
    res = TestClient(app).post("/api/messages", headers=KEY, data={
        "device_id": DEVICE, "room_name": room, "sender_name": sender, "message_text": text,
        "is_group_chat": "true", "posted_at": str(int(time.time() * 1000))})
    assert res.status_code == 201
    return res.json()["id"]


def _rows(settings, mid):
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM sms_queue WHERE message_id = %s ORDER BY id", (mid,))
        s = list(cur.fetchall())  # MySQL 은 빈 결과를 tuple 로 준다
        cur.execute("SELECT * FROM reply_queue WHERE message_id = %s AND kind = 'vendor' ORDER BY id", (mid,))
        k = list(cur.fetchall())
    return s, k


def _setup(admin):
    """★문자빌라(건물) · CCTV 분류: 건물 지정 업체 = 톡방 있는 '문자CCTV' (+업체 연락처 2명)"""
    cat = next(c for c in admin.get("/api/admin/categories").json()["items"] if c["name"] == "CCTV")
    for room in ("●문자CCTV", "★문자빌라", "★문자빌라2"):
        _post(room, "관리", "안녕하세요 처음 인사드립니다")
    admin.post("/api/admin/vendors/sync")
    admin.post("/api/admin/apartments/sync")
    vendor = next(v for v in admin.get("/api/admin/vendors").json()["items"] if v["room_name"] == "●문자CCTV")
    admin.put(f"/api/admin/vendors/{vendor['id']}/contacts", json={"contacts": [{"phone": "010-1111-0001"}, {"phone": "010-1111-0002"}]})
    apts = {a["room_name"]: a for a in admin.get("/api/admin/apartments").json()["items"]}
    for room in ("★문자빌라", "★문자빌라2"):
        r = admin.put(f"/api/admin/apartments/{apts[room]['id']}/vendors",
                      json={"assignments": [{"category_id": cat["id"], "mode": "vendor", "vendor_id": vendor["id"]}]})
        assert r.status_code == 200
    return cat, vendor, apts["★문자빌라"], apts["★문자빌라2"]


def test_sms_settings_and_building_contacts(admin, settings):
    d = store.SETTING_DEFAULTS  # 기본: 문자 켬 · 테스트 모드 · 테스트 번호
    assert (d["sms_enabled"], d["sms_test_mode"], d["sms_test_number"]) == ("1", "1", "01075728018")
    s = admin.put("/api/admin/sms", json={"enabled": True, "test_mode": True, "test_number": "01075728018"}).json()
    assert s["enabled"] is True and s["test_mode"] is True and s["test_number"] == "01075728018"
    r = admin.put("/api/admin/sms", json={"sender": "02-123-4567", "account": "acct", "api_key": "secret-key"}).json()
    assert r["sender"] == "021234567" and r["configured"] is True and r["account"].startswith("acc") and "secret" not in str(r)
    assert admin.put("/api/admin/sms", json={"test_number": "12ab"}).status_code == 422
    assert admin.put("/api/admin/sms", json={"test_number": ""}).status_code == 422  # 테스트 모드에서 번호 비움 불가

    cat, vendor, apt, _ = _setup(admin)
    view = admin.get(f"/api/admin/apartments/{apt['id']}/vendors").json()
    v = next(x for x in view["vendors"] if x["id"] == vendor["id"])
    assert [c["phone"] for c in v["directory"]] == ["010-1111-0001", "010-1111-0002"] and v["contacts"] == []
    r = admin.put(f"/api/admin/apartments/{apt['id']}/vendor-contacts/{vendor['id']}",
                  json={"contacts": [{"phone": "010 2222 0001", "name": "김기사"}, {"phone": "01022220001"}, {"phone": ""}]})
    assert r.status_code == 200 and r.json()["contacts"] == [{"phone": "010-2222-0001", "name": "김기사"}]  # 정리·중복 제거
    assert admin.put(f"/api/admin/apartments/{apt['id']}/vendor-contacts/{vendor['id']}", json={"contacts": [{"phone": "123"}]}).status_code == 422
    assert admin.put(f"/api/admin/apartments/{apt['id']}/vendor-contacts/99999999", json={"contacts": []}).status_code == 404
    v = next(x for x in admin.get(f"/api/admin/apartments/{apt['id']}/vendors").json()["vendors"] if x["id"] == vendor["id"])
    assert v["contacts"] == [{"phone": "010-2222-0001", "name": "김기사"}]


def test_test_mode_sends_every_sms_to_test_number_and_keeps_kakao(admin, settings):
    admin.put("/api/admin/sms", json={"enabled": True, "test_mode": True, "test_number": "010-7572-8018"})
    _, vendor, apt, _ = _setup(admin)
    admin.put(f"/api/admin/apartments/{apt['id']}/vendor-contacts/{vendor['id']}", json={"contacts": [{"phone": "010-2222-0001"}]})
    mid = _post("★문자빌라", "301호", "1층 CCTV 화면이 안 나와요 확인 부탁드립니다")
    store.classify_once(settings, client=Gem())
    s, k = _rows(settings, mid)
    assert [(r["phone"], r["test"], r["target_phone"]) for r in s] == [("01075728018", 1, "01022220001")]
    assert s[0]["text"].startswith("[테스트 · 실제 대상: 문자CCTV / 문자 010-2222-0001]")
    assert [r["room_name"] for r in k] == ["●문자CCTV"]  # 카톡방 요청은 예전처럼

    # 담당 업체가 없는 민원도 테스트 번호로 한 통
    lonely = _post("일반문자방", "홍길동", "CCTV 녹화가 안 되는 것 같아요 봐주세요")
    store.classify_once(settings, client=Gem())
    s2, _ = _rows(settings, lonely)
    assert len(s2) == 1 and s2[0]["phone"] == "01075728018" and "담당 업체 없음" in s2[0]["text"]

    fake = FakePpurio()
    admin.put("/api/admin/sms", json={"sender": "0212345678"})
    assert sms_dispatch.send_pending(settings, client=fake)["sent"] >= 2
    assert {to for to, _ in fake.sent} == {"01075728018"}
    hist = admin.get("/api/admin/dispatch-history", params={"view": "test"}).json()
    assert any(h["message_id"] == mid and h["status"] == "sent" for h in hist["items"])


def test_real_mode_sms_first_then_kakao_and_fallback(admin, settings):
    admin.put("/api/admin/sms", json={"enabled": True, "test_mode": False, "sender": "0212345678"})
    _, vendor, apt, apt2 = _setup(admin)
    admin.put(f"/api/admin/apartments/{apt['id']}/vendor-contacts/{vendor['id']}", json={"contacts": [{"phone": "010-3333-0001"}]})
    admin.put(f"/api/admin/apartments/{apt2['id']}/vendor-contacts/{vendor['id']}", json={"contacts": []})

    # 1) 건물×업체 담당자 번호가 있으면 문자만
    m1 = _post("★문자빌라", "302호", "주차장 CCTV 꺼져 있어요 확인 부탁드립니다")
    # 2) 그 건물에 담당자 번호가 없고 업체 연락처가 여럿 → 카톡방
    m2 = _post("★문자빌라2", "102호", "현관 CCTV 렌즈가 깨졌어요 교체 부탁드립니다")
    store.classify_once(settings, client=Gem())
    s1, k1 = _rows(settings, m1)
    assert [(r["phone"], r["test"]) for r in s1] == [("01033330001", 0)] and k1 == []
    s2, k2 = _rows(settings, m2)
    assert s2 == [] and [r["room_name"] for r in k2] == ["●문자CCTV"]
    # 업체 연락처가 한 명뿐이면 그 번호로
    admin.put(f"/api/admin/vendors/{vendor['id']}/contacts", json={"contacts": [{"phone": "010-1111-0009"}]})
    with db.transaction(settings) as conn, conn.cursor() as cur:
        assert store.sms_numbers(cur, apt2["id"], vendor["id"]) == ["010-1111-0009"]
        assert store.sms_numbers(cur, apt["id"], vendor["id"]) == ["010-3333-0001"]  # 건물 담당자가 우선

    # 3) 보내기: 성공 / 다시 시도할 오류 / 실패 → 카톡방으로 대신
    m3 = _post("★문자빌라", "303호", "옥상 CCTV 선이 끊어졌어요 확인 부탁드립니다")
    store.classify_once(settings, client=Gem())
    fake = FakePpurio()
    sms_dispatch.send_pending(settings, client=fake)
    assert ("01033330001", next(r["text"] for r in _rows(settings, m1)[0])) in fake.sent
    with db.transaction(settings) as conn, conn.cursor() as cur:  # m3 문자를 대기로 돌려 실패를 시험
        cur.execute("UPDATE sms_queue SET status = 'pending', attempts = 0, done_at = NULL WHERE message_id = %s", (m3,))
    retry = FakePpurio({"01033330001": sms.PpurioError("뿌리오 3008: 요청이 너무 많습니다", retry=True, code="3008")})
    sms_dispatch.send_pending(settings, client=retry)
    s3, k3 = _rows(settings, m3)
    assert s3[0]["status"] == "pending" and s3[0]["attempts"] == 1 and k3 == []
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("UPDATE sms_queue SET send_after = %s WHERE message_id = %s", (store.now_local(settings), m3))
    bad = FakePpurio({"01033330001": sms.PpurioError("뿌리오 2000: 요청 값이 올바르지 않습니다", retry=False, code="2000")})
    sms_dispatch.send_pending(settings, client=bad)
    s3, k3 = _rows(settings, m3)
    assert s3[0]["status"] == "failed" and "대신 보냄" in s3[0]["note"] and [r["room_name"] for r in k3] == ["●문자CCTV"]
    assert s3[0]["fallback_id"] == k3[0]["id"]
    assert admin.post(f"/api/admin/sms/{s3[0]['id']}/retry").status_code == 422  # 이미 카톡으로 대신 보냄

    # 4) 뿌리오 연동 정보가 없으면 실패 → 카톡방으로 대신
    m4 = _post("★문자빌라", "304호", "지하 CCTV 화면이 흐려요 점검 부탁드립니다")
    store.classify_once(settings, client=Gem())
    with db.transaction(settings) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM app_settings WHERE name IN ('ppurio_account', 'ppurio_api_key')")
    sms_dispatch.send_pending(settings)
    s4, k4 = _rows(settings, m4)
    assert s4[0]["status"] == "failed" and "연동 정보 없음" in s4[0]["note"] and len(k4) == 1

    # 이력: 문자 보냄 / 카톡으로 보냄 / 대기 / 실패
    def view(v):
        return admin.get("/api/admin/dispatch-history", params={"view": v}).json()

    assert any(h["message_id"] == m1 and h["channel"] == "sms" for h in view("sms")["items"])
    pend = view("pending")["items"]
    assert any(h["message_id"] == m2 and h["channel"] == "kakao" for h in pend)
    assert any(h["message_id"] == m3 and h["channel"] == "kakao" and h["after_sms_failed"] for h in pend)
    assert any(h["message_id"] == m4 and h["channel"] == "sms" for h in view("failed")["items"])
    counts = view("all")["counts"]
    assert counts["sms"] >= 1 and counts["pending"] >= 2 and counts["failed"] >= 2
    assert admin.get("/api/admin/dispatch-history", params={"view": "x"}).status_code == 422
    # 민원 대시보드에도 문자 결과
    g = next(g for g in admin.get("/api/admin/complaints").json()["items"] if g["ids"][0] == m1)
    assert g["vendor_sms"] and g["vendor_sms"][0]["status"] == "sent"
    admin.put("/api/admin/sms", json={"test_mode": True})
