import sys
import os
import io
import time
import uuid
import json
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath("backend"))

from app.main import app
from app.core.database import SessionLocal
from app.models.user import User
from app.models.project import VideoProject


def run_journey():
    print("=" * 80)
    print("PHASE 10: FRONTEND API JOURNEY INTEGRATION TEST")
    print("=" * 80)

    client = TestClient(app)
    test_email = f"phase10_{uuid.uuid4().hex[:8]}@example.com"
    test_password = "Password1234!"
    test_name = "Phase 10 Test User"

    # 1. Register
    print("\n[TEST 1] Registering new user...")
    reg_resp = client.post("/api/v1/auth/register", json={
        "email": test_email,
        "password": test_password,
        "full_name": test_name
    })
    print(f"  Register Status: {reg_resp.status_code}")
    assert reg_resp.status_code == 201, f"Register failed: {reg_resp.text}"
    user_data = reg_resp.json()
    print(f"  Registered User ID: {user_data.get('id')}, Email: {user_data.get('email')}")
    assert "video_gen_session" in client.cookies, "Session cookie not set after registration!"
    print("  ✓ Session cookie received and stored in client.")

    # 2. Get Me (Session persistence)
    print("\n[TEST 2] Verifying session persistence via GET /auth/me...")
    me_resp = client.get("/api/v1/auth/me")
    print(f"  GET /auth/me Status: {me_resp.status_code}")
    assert me_resp.status_code == 200, f"GET /auth/me failed: {me_resp.text}"
    me_data = me_resp.json()
    assert me_data["email"] == test_email.lower(), "User email mismatch!"
    print(f"  ✓ Authenticated as: {me_data['full_name']} ({me_data['email']})")

    # 3. Logout
    print("\n[TEST 3] Testing Logout via POST /auth/logout...")
    logout_resp = client.post("/api/v1/auth/logout")
    print(f"  POST /auth/logout Status: {logout_resp.status_code}")
    assert logout_resp.status_code == 200, f"Logout failed: {logout_resp.text}"
    
    # Verify session is invalidated
    unauth_resp = client.get("/api/v1/auth/me")
    print(f"  Subsequent GET /auth/me Status: {unauth_resp.status_code} (Expected 401)")
    assert unauth_resp.status_code == 401, "Session was not invalidated after logout!"
    print("  ✓ Session successfully destroyed and protected routes blocked.")

    # 4. Login
    print("\n[TEST 4] Testing Login via POST /auth/login...")
    login_resp = client.post("/api/v1/auth/login", json={
        "email": test_email,
        "password": test_password
    })
    print(f"  POST /auth/login Status: {login_resp.status_code}")
    assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    login_data = login_resp.json()
    print(f"  Logged in as: {login_data.get('user', {}).get('email')}")
    assert "video_gen_session" in client.cookies, "Session cookie missing after login!"
    print("  ✓ Login succeeded and new session cookie established.")

    # 5. List Projects (empty initially)
    print("\n[TEST 5] Listing projects via GET /projects...")
    list_resp = client.get("/api/v1/projects")
    assert list_resp.status_code == 200
    projects = list_resp.json()
    print(f"  Existing projects count: {len(projects)}")
    print("  ✓ Projects list endpoint reachable.")

    # 6. Create Project
    print("\n[TEST 6] Creating project via POST /projects...")
    create_payload = {
        "title": "Phase 10 Frontend Verification Video",
        "occasion": "Travel",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "music_mood": "Cinematic",
        "description": "Created to verify frontend integration"
    }
    create_resp = client.post("/api/v1/projects", json=create_payload)
    print(f"  POST /projects Status: {create_resp.status_code}")
    assert create_resp.status_code == 201, f"Project creation failed: {create_resp.text}"
    proj = create_resp.json()
    proj_id = proj["id"]
    print(f"  Created Project ID: {proj_id}")
    print(f"  Title: '{proj['title']}', Status: {proj['status']}, Created At: {proj.get('created_at')}")
    assert proj.get("created_at") is not None, "created_at missing from Project response!"
    print("  ✓ Project created with proper status and creation date.")

    # 7. Upload Images (up to 5)
    print("\n[TEST 7] Uploading reference images via POST /projects/{id}/images...")
    # Generate 3 dummy JPEG files in memory
    from PIL import Image
    files = []
    for i in range(3):
        img_byte_arr = io.BytesIO()
        im = Image.new("RGB", (300, 300), color=(50 * (i + 1), 70 * (i + 1), 90))
        im.save(img_byte_arr, format="JPEG")
        img_byte_arr.seek(0)
        files.append(("files", (f"test_photo_{i+1}.jpg", img_byte_arr.getvalue(), "image/jpeg")))

    upload_resp = client.post(f"/api/v1/projects/{proj_id}/images", files=files)
    print(f"  POST /projects/{proj_id}/images Status: {upload_resp.status_code}")
    assert upload_resp.status_code == 201, f"Upload failed: {upload_resp.text}"
    uploaded = upload_resp.json()
    print(f"  Uploaded Images Count: {len(uploaded)}")
    assert len(uploaded) == 3, f"Expected 3 images, got {len(uploaded)}"
    print("  ✓ 3 reference images uploaded and registered successfully.")

    # 8. Get Project Details
    print("\n[TEST 8] Fetching project detail via GET /projects/{id}...")
    detail_resp = client.get(f"/api/v1/projects/{proj_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    print(f"  Project Details: Title='{detail['title']}', Images count={len(detail.get('images', []))}")
    assert len(detail.get("images", [])) == 3
    print("  ✓ Project detail endpoint returns images array and full metadata.")

    # 9. Music Library & Track Selection
    print("\n[TEST 9] Browsing music library and selecting a track...")
    cat_resp = client.get("/api/v1/music/categories")
    assert cat_resp.status_code == 200
    categories = cat_resp.json()
    print(f"  Music Categories ({len(categories)}): {categories}")
    assert len(categories) > 0, "No music categories returned!"

    tracks_resp = client.get("/api/v1/music/tracks?category=cinematic")
    assert tracks_resp.status_code == 200
    tracks = tracks_resp.json()
    print(f"  Cinematic tracks found: {len(tracks)}")
    assert len(tracks) > 0, "No cinematic tracks found in library!"
    selected_track = tracks[0]
    print(f"  Selecting Track: '{selected_track['title']}' ({selected_track['id']})")

    sel_resp = client.post(f"/api/v1/projects/{proj_id}/select-music", json={
        "track_id": selected_track["id"],
        "provider": "library"
    })
    assert sel_resp.status_code in [200, 201]
    sel_data = sel_resp.json()
    print(f"  Selected Music Asset ID: {sel_data.get('id')}, Provider: {sel_data.get('provider')}")
    assert sel_data.get("provider") == "static_library"
    print("  ✓ Music track selected; provider is 'static_library'.")

    # 10. Check Final Video Endpoint
    print("\n[TEST 10] Checking GET /projects/{id}/final-video status...")
    fv_resp = client.get(f"/api/v1/projects/{proj_id}/final-video")
    assert fv_resp.status_code == 200
    fv_data = fv_resp.json()
    print(f"  Final Video Status: {fv_data.get('status')} (Expected NOT_STARTED)")
    assert fv_data.get("status") == "NOT_STARTED"
    print("  ✓ Final video status returns NOT_STARTED prior to composition.")

    # 11. Delete Project
    print("\n[TEST 11] Deleting project via DELETE /projects/{id}...")
    del_resp = client.delete(f"/api/v1/projects/{proj_id}")
    assert del_resp.status_code in [200, 204]
    print(f"  DELETE /projects/{proj_id} Status: {del_resp.status_code}")

    # Confirm deletion
    get_after_del = client.get(f"/api/v1/projects/{proj_id}")
    assert get_after_del.status_code == 404
    print("  ✓ Project and associated images cleaned up successfully.")

    # Clean up test user
    db = SessionLocal()
    u = db.query(User).filter(User.email == test_email.lower()).first()
    if u:
        db.delete(u)
        db.commit()
    db.close()
    print("  ✓ Test user removed from database.")

    print("\n" + "=" * 80)
    print("ALL 11 FRONTEND INTEGRATION WORKFLOW TESTS PASSED!")
    print("=" * 80)


if __name__ == "__main__":
    run_journey()
