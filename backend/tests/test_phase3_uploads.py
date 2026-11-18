import io
import os
import uuid
import pytest
from PIL import Image
from app.core.config import settings
from app.services.storage import get_storage_service


def make_image_bytes(fmt: str = "JPEG", size=(200, 200), color=(255, 0, 0)) -> bytes:
    """Generate valid in-memory image bytes for testing."""
    buf = io.BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def auth_user_a(client):
    """Register and login User A."""
    email = f"usera_{uuid.uuid4().hex[:6]}@example.com"
    pwd = "Password123!"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": pwd,
        "full_name": "User Alpha"
    })
    client.post("/api/v1/auth/login", json={"email": email, "password": pwd})
    return email


@pytest.fixture
def auth_user_b(client):
    """Register and login User B."""
    email = f"userb_{uuid.uuid4().hex[:6]}@example.com"
    pwd = "Password123!"
    # Save user A's cookies
    old_cookies = dict(client.cookies)
    client.cookies.clear()

    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": pwd,
        "full_name": "User Beta"
    })
    client.post("/api/v1/auth/login", json={"email": email, "password": pwd})
    b_cookies = dict(client.cookies)

    # Restore user A's cookies
    client.cookies.clear()
    client.cookies.update(old_cookies)
    return b_cookies


# 1. Successful project creation
def test_1_successful_project_creation(client, auth_user_a):
    payload = {
        "title": "My Travel Memories 2026",
        "occasion": "Travel",
        "duration_seconds": 20,
        "aspect_ratio": "9:16",
        "music_mood": "Cinematic",
        "custom_music_prompt": "Epic brass and gentle strings",
        "description": "A wonderful trek through the Swiss Alps."
    }
    res = client.post("/api/v1/projects", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["title"] == "My Travel Memories 2026"
    assert data["occasion"] == "Travel"
    assert data["duration_seconds"] == 20
    assert data["aspect_ratio"] == "9:16"
    assert data["music_mood"] == "Cinematic"
    assert data["status"] == "DRAFT"
    assert data["image_count"] == 0
    assert "id" in data


# 2. Successful upload of one image
def test_2_successful_upload_one_image(client, auth_user_a):
    # Create project
    proj_res = client.post("/api/v1/projects", json={
        "title": "Single Image Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "16:9",
        "music_mood": "Happy",
    })
    proj_id = proj_res.json()["id"]

    img_bytes = make_image_bytes("JPEG", (300, 400), (0, 128, 255))
    files = [("files", ("photo1.jpg", img_bytes, "image/jpeg"))]

    up_res = client.post(f"/api/v1/projects/{proj_id}/images", files=files)
    assert up_res.status_code == 201
    images = up_res.json()
    assert len(images) == 1
    img = images[0]
    assert img["original_filename"] == "photo1.jpg"
    assert img["mime_type"] == "image/jpeg"
    assert img["width"] == 300
    assert img["height"] == 400
    assert img["order_index"] == 0
    assert img["file_size"] == len(img_bytes)
    assert "url" in img
    assert "/images/" in img["url"]
    # Path traversal / safe storage check: internal absolute path must NOT be exposed
    assert "D:" not in str(img)
    assert "C:" not in str(img)
    assert "storage" not in img.get("file_path", "")


# 3. Successful upload of five images
def test_3_successful_upload_five_images(client, auth_user_a):
    proj_res = client.post("/api/v1/projects", json={
        "title": "Five Images Showcase",
        "occasion": "Celebration",
        "duration_seconds": 30,
        "aspect_ratio": "1:1",
        "music_mood": "Celebration",
    })
    proj_id = proj_res.json()["id"]

    # Upload 5 diverse images (JPG, PNG, WEBP)
    files = [
        ("files", ("pic1.jpg", make_image_bytes("JPEG", (100, 100), (255, 0, 0)), "image/jpeg")),
        ("files", ("pic2.png", make_image_bytes("PNG", (150, 150), (0, 255, 0)), "image/png")),
        ("files", ("pic3.webp", make_image_bytes("WEBP", (200, 200), (0, 0, 255)), "image/webp")),
        ("files", ("pic4.jpeg", make_image_bytes("JPEG", (250, 250), (255, 255, 0)), "image/jpeg")),
        ("files", ("pic5.png", make_image_bytes("PNG", (300, 300), (255, 0, 255)), "image/png")),
    ]

    up_res = client.post(f"/api/v1/projects/{proj_id}/images", files=files)
    assert up_res.status_code == 201
    images = up_res.json()
    assert len(images) == 5

    # Verify strict order preservation (0, 1, 2, 3, 4)
    for idx, im in enumerate(images):
        assert im["order_index"] == idx
        assert im["original_filename"] == f"pic{idx + 1}." + ("jpg" if idx == 0 else "png" if idx in (1, 4) else "webp" if idx == 2 else "jpeg")


# 4. Rejection of a sixth image
def test_4_rejection_of_sixth_image(client, auth_user_a):
    proj_res = client.post("/api/v1/projects", json={
        "title": "Max Limit Test",
        "occasion": "Anniversary",
        "duration_seconds": 20,
    })
    proj_id = proj_res.json()["id"]

    # Upload 5 images first
    files_5 = [
        ("files", (f"img_{i}.jpg", make_image_bytes("JPEG"), "image/jpeg"))
        for i in range(5)
    ]
    up1 = client.post(f"/api/v1/projects/{proj_id}/images", files=files_5)
    assert up1.status_code == 201

    # Attempt to upload 6th image
    files_6th = [("files", ("extra.jpg", make_image_bytes("JPEG"), "image/jpeg"))]
    up2 = client.post(f"/api/v1/projects/{proj_id}/images", files=files_6th)
    assert up2.status_code == 400
    err = up2.json()
    assert "Maximum 5 images allowed" in err["error"]["message"]


# 5. Rejection of unsupported file formats
def test_5_rejection_of_unsupported_formats(client, auth_user_a):
    proj_res = client.post("/api/v1/projects", json={"title": "Format Check"})
    proj_id = proj_res.json()["id"]

    # 1. Plain text file with .txt extension
    txt_file = [("files", ("notes.txt", b"Hello world text file", "text/plain"))]
    r1 = client.post(f"/api/v1/projects/{proj_id}/images", files=txt_file)
    assert r1.status_code == 400
    assert "Unsupported file extension" in r1.json()["error"]["message"]

    # 2. PDF file
    pdf_file = [("files", ("document.pdf", b"%PDF-1.4 header content", "application/pdf"))]
    r2 = client.post(f"/api/v1/projects/{proj_id}/images", files=pdf_file)
    assert r2.status_code == 400

    # 3. Spoofed file: text content renamed to .jpg
    spoofed = [("files", ("malicious.jpg", b"This is secretly not a JPEG", "image/jpeg"))]
    r3 = client.post(f"/api/v1/projects/{proj_id}/images", files=spoofed)
    assert r3.status_code == 400
    assert "not a valid or readable image" in r3.json()["error"]["message"]


# 6. Rejection of invalid or oversized files
def test_6_rejection_of_invalid_or_oversized_files(client, auth_user_a):
    proj_res = client.post("/api/v1/projects", json={"title": "Size Check"})
    proj_id = proj_res.json()["id"]

    # 1. Empty 0-byte file
    empty_file = [("files", ("empty.jpg", b"", "image/jpeg"))]
    r1 = client.post(f"/api/v1/projects/{proj_id}/images", files=empty_file)
    assert r1.status_code == 400
    assert "empty" in r1.json()["error"]["message"]

    # 2. Oversized file (> 25MB)
    oversized_bytes = b"X" * (26 * 1024 * 1024)
    oversized_file = [("files", ("huge.jpg", oversized_bytes, "image/jpeg"))]
    r2 = client.post(f"/api/v1/projects/{proj_id}/images", files=oversized_file)
    assert r2.status_code == 400
    assert "exceeds the maximum allowed size" in r2.json()["error"]["message"]


# 7. Image listing
def test_7_image_listing(client, auth_user_a):
    proj_res = client.post("/api/v1/projects", json={"title": "Listing Test"})
    proj_id = proj_res.json()["id"]

    # Upload 2 images sequentially
    client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[("files", ("first.png", make_image_bytes("PNG"), "image/png"))]
    )
    client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[("files", ("second.jpg", make_image_bytes("JPEG"), "image/jpeg"))]
    )

    list_res = client.get(f"/api/v1/projects/{proj_id}/images")
    assert list_res.status_code == 200
    images = list_res.json()
    assert len(images) == 2
    assert images[0]["original_filename"] == "first.png"
    assert images[0]["order_index"] == 0
    assert images[1]["original_filename"] == "second.jpg"
    assert images[1]["order_index"] == 1


# 8. Image deletion
def test_8_image_deletion(client, auth_user_a):
    proj_res = client.post("/api/v1/projects", json={"title": "Deletion Test"})
    proj_id = proj_res.json()["id"]

    # Upload 3 images
    files = [
        ("files", (f"img_{i}.jpg", make_image_bytes("JPEG"), "image/jpeg"))
        for i in range(3)
    ]
    up_res = client.post(f"/api/v1/projects/{proj_id}/images", files=files)
    images = up_res.json()
    img_to_delete_id = images[1]["id"]  # Delete the middle image (order 1)

    del_res = client.delete(f"/api/v1/projects/{proj_id}/images/{img_to_delete_id}")
    assert del_res.status_code == 204

    # Verify listing now has 2 images with re-indexed order (0, 1)
    list_res = client.get(f"/api/v1/projects/{proj_id}/images")
    remaining = list_res.json()
    assert len(remaining) == 2
    assert remaining[0]["id"] == images[0]["id"]
    assert remaining[0]["order_index"] == 0
    assert remaining[1]["id"] == images[2]["id"]
    assert remaining[1]["order_index"] == 1


# 9. Unauthorized upload rejection
def test_9_unauthorized_upload_rejection(client, auth_user_a):
    # Create project as user A
    proj_res = client.post("/api/v1/projects", json={"title": "Auth Guard Test"})
    proj_id = proj_res.json()["id"]

    # Clear cookies to simulate unauthenticated request
    client.cookies.clear()

    files = [("files", ("unauth.jpg", make_image_bytes("JPEG"), "image/jpeg"))]
    res = client.post(f"/api/v1/projects/{proj_id}/images", files=files)
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "AUTHENTICATION_FAILED"


# 10. Cross-user project access rejection
def test_10_cross_user_project_access_rejection(client, auth_user_a, auth_user_b):
    # User A creates a project and uploads an image
    proj_res = client.post("/api/v1/projects", json={"title": "User A Private Project"})
    proj_id = proj_res.json()["id"]

    up_res = client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[("files", ("secret_a.jpg", make_image_bytes("JPEG"), "image/jpeg"))]
    )
    img_id = up_res.json()[0]["id"]

    # Switch to User B's session cookies
    client.cookies.clear()
    client.cookies.update(auth_user_b)

    # 1. User B tries to upload to User A's project -> 403 Forbidden
    b_upload = client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[("files", ("b_intruder.jpg", make_image_bytes("JPEG"), "image/jpeg"))]
    )
    assert b_upload.status_code == 403
    assert b_upload.json()["error"]["code"] == "PERMISSION_DENIED"

    # 2. User B tries to list User A's images -> 403 Forbidden
    b_list = client.get(f"/api/v1/projects/{proj_id}/images")
    assert b_list.status_code == 403

    # 3. User B tries to view/stream User A's image file -> 403 Forbidden
    b_stream = client.get(f"/api/v1/projects/{proj_id}/images/{img_id}/file")
    assert b_stream.status_code == 403

    # 4. User B tries to delete User A's image -> 403 Forbidden
    b_del = client.delete(f"/api/v1/projects/{proj_id}/images/{img_id}")
    assert b_del.status_code == 403


# 11. Project deletion and associated image cleanup
def test_11_project_deletion_and_image_cleanup(client, auth_user_a):
    proj_res = client.post("/api/v1/projects", json={"title": "To Be Deleted With Files"})
    proj_id = proj_res.json()["id"]

    # Upload 2 images
    files = [
        ("files", ("clean1.jpg", make_image_bytes("JPEG"), "image/jpeg")),
        ("files", ("clean2.png", make_image_bytes("PNG"), "image/png")),
    ]
    up_res = client.post(f"/api/v1/projects/{proj_id}/images", files=files)
    assert up_res.status_code == 201

    storage = get_storage_service()
    # Confirm physical files exist in storage directory
    proj_dir = os.path.join(storage.base_dir, "projects", str(proj_id))
    assert os.path.isdir(proj_dir)
    assert len(os.listdir(proj_dir)) == 2

    # Delete project
    del_res = client.delete(f"/api/v1/projects/{proj_id}")
    assert del_res.status_code == 204

    # Verify project is gone from API
    get_res = client.get(f"/api/v1/projects/{proj_id}")
    assert get_res.status_code == 404

    # Verify physical files were cleaned up from storage
    remaining_files = os.listdir(proj_dir) if os.path.isdir(proj_dir) else []
    assert len(remaining_files) == 0, f"Expected 0 files remaining, found: {remaining_files}"
