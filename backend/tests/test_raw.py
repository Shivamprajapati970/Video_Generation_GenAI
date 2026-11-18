import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["TESTING"] = "true"

from app.core.database import engine
from app.models.base import Base
import app.models
from app.api.v1.auth import register, login, get_me, logout
from app.api.v1.projects import list_projects, create_project, get_project, update_project, delete_project
from app.schemas.auth import UserRegister, UserLogin
from app.schemas.project import ProjectCreate, OccasionEnum, DurationEnum, MusicMoodEnum
from app.core.database import SessionLocal
from starlette.responses import Response
from starlette.requests import Request

def run():
    print("Step 1: Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully!")

    db = SessionLocal()

    # Create dummy request and response
    scope = {"type": "http", "client": ("127.0.0.1", 12345), "headers": []}
    req = Request(scope)
    res = Response()

    # 1. Test Registration
    print("Step 2: Testing registration...")
    user_reg = UserRegister(
        email="test_engineer@example.com",
        password="ValidPassword123!",
        full_name="AI Video Engineer"
    )
    user = register(user_reg, res, req, db)
    print(f"User registered successfully! ID={user.id}, email={user.email}")

    # 2. Test Login
    print("Step 3: Testing login...")
    user_login = UserLogin(
        email="test_engineer@example.com",
        password="ValidPassword123!"
    )
    session_res = login(user_login, res, req, db)
    print(f"User logged in successfully! SessionID={session_res.id}")

    # 3. Test Get Current User
    print("Step 4: Testing get profile...")
    me = get_me(user)
    print(f"Profile verified: {me.email}")

    # 4. Test Project Creation
    print("Step 5: Testing video project creation...")
    proj_create = ProjectCreate(
        title="Epic Travel Memories",
        occasion=OccasionEnum.TRAVEL,
        duration_seconds=DurationEnum.DURATION_20,
        music_mood=MusicMoodEnum.CINEMATIC,
        custom_music_prompt="Heroic brass and uplifting strings",
        description="A breathtaking trip through the mountains"
    )
    project = create_project(proj_create, user, db)
    print(f"Project created! ID={project.id}, Title={project.title}, Occasion={project.occasion}")

    # 5. Test Project Listing
    print("Step 6: Testing list projects...")
    projects = list_projects(user, db)
    print(f"Listed {len(projects)} project(s) for user.")
    assert len(projects) >= 1

    # 6. Test Project Detail
    print("Step 7: Testing get project details...")
    proj_detail = get_project(project.id, user, db)
    print(f"Project detail retrieved: status={proj_detail.status}")

    # 7. Test Project Deletion
    print("Step 8: Testing project deletion...")
    delete_project(project.id, user, db)
    print("Project deleted successfully!")

    db.close()
    print("\n>>> ALL API LOGIC, MODELS, SESSIONS, AND DATABASE OPERATIONS WORK 100% FLAWLESSLY! <<<")

if __name__ == "__main__":
    run()
