import io
import os
import uuid
import pytest
from PIL import Image
from app.core.config import settings
from app.services.storage import get_storage_service


def make_test_image(size=(120, 120), color=(10, 20, 30), fmt="JPEG") -> bytes:
    """Create genuine image bytes with custom dimensions and color."""
    buf = io.BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def test_user(client):
    """Register and login a clean test user."""
    email = f"check_{uuid.uuid4().hex[:6]}@example.com"
    pwd = "Password123!"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": pwd,
        "full_name": "Verification User"
    })
    client.post("/api/v1/auth/login", json={"email": email, "password": pwd})
    return email


# ---------------------------------------------------------------------------
# Point 1: Multi-request upload limit enforcement
# ---------------------------------------------------------------------------

def test_point_1_multi_request_upload_limit(client, test_user):
    """Confirm 5-image limit is strictly enforced across multiple requests."""
    proj = client.post("/api/v1/projects", json={"title": "Multi-request Limit Test"}).json()
    proj_id = proj["id"]

    # Request 1: Upload 2 images -> current: 2, remaining: 3
    r1 = client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[
            ("files", ("img1.jpg", make_test_image(), "image/jpeg")),
            ("files", ("img2.jpg", make_test_image(), "image/jpeg")),
        ]
    )
    assert r1.status_code == 201
    assert len(r1.json()) == 2

    # Request 2: Upload 2 more images -> current: 4, remaining: 1
    r2 = client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[
            ("files", ("img3.png", make_test_image(fmt="PNG"), "image/png")),
            ("files", ("img4.png", make_test_image(fmt="PNG"), "image/png")),
        ]
    )
    assert r2.status_code == 201
    assert len(r2.json()) == 2

    # Request 3: Attempt to upload 2 images when only 1 slot remains -> REJECTED (4 + 2 = 6 > 5)
    r3 = client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[
            ("files", ("img5.jpg", make_test_image(), "image/jpeg")),
            ("files", ("img6.jpg", make_test_image(), "image/jpeg")),
        ]
    )
    assert r3.status_code == 400
    err3 = r3.json()["error"]["message"]
    assert "Maximum 5 images allowed" in err3
    assert "currently 4 uploaded" in err3

    # Request 4: Upload exactly 1 image -> current: 5 (reaches max limit)
    r4 = client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[("files", ("img5.webp", make_test_image(fmt="WEBP"), "image/webp"))]
    )
    assert r4.status_code == 201
    assert len(r4.json()) == 1

    # Request 5: Attempt any additional upload on full project -> REJECTED (5 + 1 = 6 > 5)
    r5 = client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[("files", ("overflow.jpg", make_test_image(), "image/jpeg"))]
    )
    assert r5.status_code == 400
    err5 = r5.json()["error"]["message"]
    assert "currently 5 uploaded" in err5


# ---------------------------------------------------------------------------
# Point 2: Image cleanup & transaction safety
# ---------------------------------------------------------------------------

def test_point_2_transaction_safe_image_cleanup(client, test_user):
    """Confirm file deletion occurs after DB commit and failed uploads do not leak files."""
    proj = client.post("/api/v1/projects", json={"title": "Cleanup Test"}).json()
    proj_id = proj["id"]
    storage = get_storage_service()

    # 1. Upload 2 valid images
    up = client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[
            ("files", ("pic_a.jpg", make_test_image(), "image/jpeg")),
            ("files", ("pic_b.jpg", make_test_image(), "image/jpeg")),
        ]
    )
    images = up.json()
    img_a_id = images[0]["id"]

    # Verify project folder exists on disk with 2 files
    proj_dir = os.path.join(storage.base_dir, "projects", proj_id)
    assert os.path.isdir(proj_dir)
    assert len(os.listdir(proj_dir)) == 2

    # 2. Delete single image
    del_img = client.delete(f"/api/v1/projects/{proj_id}/images/{img_a_id}")
    assert del_img.status_code == 204
    # Exactly 1 file should remain on disk
    assert len(os.listdir(proj_dir)) == 1

    # 3. Test failed upload cleanup: send 1 valid image + 1 corrupted/invalid file in batch
    corrupted_batch = [
        ("files", ("good.jpg", make_test_image(), "image/jpeg")),
        ("files", ("bad.jpg", b"NOT AN IMAGE CONTENT", "image/jpeg")),
    ]
    failed_up = client.post(f"/api/v1/projects/{proj_id}/images", files=corrupted_batch)
    assert failed_up.status_code == 400
    # Confirm NO new orphan file was leaked on disk from good.jpg
    assert len(os.listdir(proj_dir)) == 1

    # 4. Delete entire project
    del_proj = client.delete(f"/api/v1/projects/{proj_id}")
    assert del_proj.status_code == 204
    # All files inside project folder should be cleaned up
    remaining = os.listdir(proj_dir) if os.path.isdir(proj_dir) else []
    assert len(remaining) == 0


# ---------------------------------------------------------------------------
# Point 3: CORS & Cookie authentication verification
# ---------------------------------------------------------------------------

def test_point_3_cors_and_cookie_delivery(client):
    """Verify cookies are delivered and validated across origins with credentials: 'include'."""
    email = f"cors_{uuid.uuid4().hex[:6]}@example.com"
    client.cookies.clear()

    # 1. Register with Origin: http://localhost:3000
    reg_res = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "CORS Tester"},
        headers={"Origin": "http://localhost:3000"}
    )
    assert reg_res.status_code == 201

    # Check CORS headers
    assert reg_res.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert reg_res.headers.get("access-control-allow-credentials") == "true"

    # Check Cookie headers
    cookie_header = reg_res.headers.get("set-cookie", "")
    assert settings.SESSION_COOKIE_NAME in cookie_header
    assert "HttpOnly" in cookie_header or "httponly" in cookie_header
    assert "Path=/" in cookie_header or "path=/" in cookie_header

    # 2. Subsequent request from Next.js origin with cookie
    me_res = client.get(
        "/api/v1/auth/me",
        headers={"Origin": "http://localhost:3000"}
    )
    assert me_res.status_code == 200
    assert me_res.json()["email"] == email
    assert me_res.headers.get("access-control-allow-origin") == "http://localhost:3000"


# ---------------------------------------------------------------------------
# Point 4: File size limit enforced on actual uploaded bytes
# ---------------------------------------------------------------------------

def test_point_4_file_size_enforced_on_actual_bytes(client, test_user):
    """Confirm file size is enforced on actual raw bytes and recorded accurately."""
    proj = client.post("/api/v1/projects", json={"title": "Byte Size Test"}).json()
    proj_id = proj["id"]

    # 1. Test empty file (0 bytes) -> rejected
    r_empty = client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[("files", ("zero.jpg", b"", "image/jpeg"))]
    )
    assert r_empty.status_code == 400
    assert "empty" in r_empty.json()["error"]["message"]

    # 2. Test valid file and verify stored file_size matches exact bytes
    raw_img = make_test_image(size=(180, 180), color=(100, 150, 200))
    exact_byte_len = len(raw_img)
    r_valid = client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[("files", ("sized.jpg", raw_img, "image/jpeg"))]
    )
    assert r_valid.status_code == 201
    img_record = r_valid.json()[0]
    assert img_record["file_size"] == exact_byte_len

    # 3. Test oversized file (> 25MB) -> rejected
    oversized_bytes = b"O" * (26 * 1024 * 1024)
    r_over = client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[("files", ("oversized.jpg", oversized_bytes, "image/jpeg"))]
    )
    assert r_over.status_code == 400
    assert "exceeds the maximum allowed size of 25MB" in r_over.json()["error"]["message"]


# ---------------------------------------------------------------------------
# Point 5: Image ordering & re-ordering endpoint
# ---------------------------------------------------------------------------

def test_point_5_image_reordering(client, test_user):
    """Confirm users can change the order of uploaded images via PUT /images/reorder."""
    proj = client.post("/api/v1/projects", json={"title": "Reorder Test"}).json()
    proj_id = proj["id"]

    # Upload 3 images: A, B, C
    up = client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[
            ("files", ("scene_A.jpg", make_test_image(color=(255, 0, 0)), "image/jpeg")),
            ("files", ("scene_B.jpg", make_test_image(color=(0, 255, 0)), "image/jpeg")),
            ("files", ("scene_C.jpg", make_test_image(color=(0, 0, 255)), "image/jpeg")),
        ]
    )
    assert up.status_code == 201
    images = up.json()
    id_a = images[0]["id"]
    id_b = images[1]["id"]
    id_c = images[2]["id"]

    # Initial order: A (0), B (1), C (2)
    assert [img["order_index"] for img in images] == [0, 1, 2]

    # Reorder to: C, A, B
    new_order = [id_c, id_a, id_b]
    reorder_res = client.put(
        f"/api/v1/projects/{proj_id}/images/reorder",
        json={"image_ids": new_order}
    )
    assert reorder_res.status_code == 200
    reordered = reorder_res.json()
    assert len(reordered) == 3
    assert reordered[0]["id"] == id_c
    assert reordered[0]["order_index"] == 0
    assert reordered[1]["id"] == id_a
    assert reordered[1]["order_index"] == 1
    assert reordered[2]["id"] == id_b
    assert reordered[2]["order_index"] == 2

    # Verify persistence via GET /projects/{id}/images
    get_res = client.get(f"/api/v1/projects/{proj_id}/images")
    assert get_res.status_code == 200
    persisted = get_res.json()
    assert persisted[0]["id"] == id_c
    assert persisted[1]["id"] == id_a
    assert persisted[2]["id"] == id_b

    # Verify invalid reorder (incomplete list or duplicate ID) -> 400 Bad Request
    invalid_res = client.put(
        f"/api/v1/projects/{proj_id}/images/reorder",
        json={"image_ids": [id_c, id_a]}  # missing id_b
    )
    assert invalid_res.status_code == 400
