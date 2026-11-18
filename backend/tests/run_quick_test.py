import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["TESTING"] = "true"
os.environ["ENVIRONMENT"] = "testing"

import asyncio
import httpx
from app.main import app
from app.models.base import Base
from app.core.database import engine
from app.core.config import settings

async def main():
    print("Initializing test tables...")
    Base.metadata.create_all(bind=engine)
    
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Test Root
        res = await client.get("/")
        print("1. Root endpoint:", res.status_code, res.json())
        assert res.status_code == 200

        # 2. Test Health
        res = await client.get("/api/v1/health")
        print("2. Health endpoint:", res.status_code, res.json())
        assert res.status_code == 200

        # 3. Test Register
        reg_payload = {
            "email": "creator@example.com",
            "password": "SecurePassword123!",
            "full_name": "Creative Director"
        }
        res = await client.post("/api/v1/auth/register", json=reg_payload)
        print("3. Register:", res.status_code, res.json())
        assert res.status_code == 201
        assert settings.SESSION_COOKIE_NAME in res.cookies
        session_cookie = res.cookies[settings.SESSION_COOKIE_NAME]

        # 4. Test /auth/me with session cookie
        res = await client.get("/api/v1/auth/me", cookies={settings.SESSION_COOKIE_NAME: session_cookie})
        print("4. Auth Me:", res.status_code, res.json())
        assert res.status_code == 200
        assert res.json()["email"] == "creator@example.com"

        # 5. Test Project Create
        proj_payload = {
            "title": "My Travel Video",
            "occasion": "Travel",
            "duration_seconds": 20,
            "music_mood": "Cinematic",
            "description": "Paris travel highlights 2026."
        }
        res = await client.post(
            "/api/v1/projects",
            json=proj_payload,
            cookies={settings.SESSION_COOKIE_NAME: session_cookie}
        )
        print("5. Create Project:", res.status_code, res.json())
        assert res.status_code == 201
        project_id = res.json()["id"]

        # 6. Test List Projects
        res = await client.get(
            "/api/v1/projects",
            cookies={settings.SESSION_COOKIE_NAME: session_cookie}
        )
        print("6. List Projects:", res.status_code, f"Found {len(res.json())} projects")
        assert res.status_code == 200
        assert len(res.json()) >= 1

        # 7. Test Get Project Detail
        res = await client.get(
            f"/api/v1/projects/{project_id}",
            cookies={settings.SESSION_COOKIE_NAME: session_cookie}
        )
        print("7. Project Detail:", res.status_code, res.json()["title"])
        assert res.status_code == 200

        # 8. Test Logout
        res = await client.post(
            "/api/v1/auth/logout",
            cookies={settings.SESSION_COOKIE_NAME: session_cookie}
        )
        print("8. Logout:", res.status_code, res.json())
        assert res.status_code == 200

        # 9. Verify unauthorized
        res = await client.get("/api/v1/projects")
        print("9. Unauthenticated project access:", res.status_code, res.json())
        assert res.status_code == 401

    print("\nALL VERIFICATION TESTS PASSED SUCCESSFULLY! 100% HEALTHY.")

if __name__ == "__main__":
    asyncio.run(main())
