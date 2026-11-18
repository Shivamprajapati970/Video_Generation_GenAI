# AI-Powered Personalized Video Generation Application

A complete full-stack AI application that turns up to 5 uploaded reference photos into personalized, cinematic videos (15s, 20s, or 30s) complete with visual transitions, custom text overlays, and AI-generated background music.

## Architecture Overview

```
Video_Generation_GenAI/
├── backend/
│   ├── alembic/                      # Database migrations
│   │   ├── versions/                 # Versioned migration scripts (0001_initial_schema.py)
│   │   └── env.py                    # Alembic migration environment
│   ├── app/
│   │   ├── api/                      # API endpoints & dependency injection
│   │   │   ├── deps.py               # Session-cookie auth & DB dependencies
│   │   │   └── v1/
│   │   │       ├── auth.py           # Register, Login, Logout, /me
│   │   │       ├── projects.py       # Video project CRUD operations
│   │   │       └── router.py         # Consolidated v1 router & /health
│   │   ├── core/
│   │   │   ├── config.py             # Pydantic Settings & environment config
│   │   │   ├── database.py           # SQLAlchemy 2.x engine & sessionmaker
│   │   │   ├── security.py           # Bcrypt hashing & cryptographically random tokens
│   │   │   └── exceptions.py         # Structured error responses
│   │   ├── models/                   # SQLAlchemy 2.0 ORM models
│   │   │   ├── base.py               # Base class & PostgreSQL JSONB compatible types
│   │   │   ├── user.py               # User model
│   │   │   ├── session.py            # Server-side Session model
│   │   │   ├── project.py            # VideoProject model (with JSONB scene_plan)
│   │   │   ├── image.py              # ProjectImage model (max 5 reference images)
│   │   │   ├── job.py                # GenerationJob model (7-stage state tracking)
│   │   │   └── asset.py              # GeneratedAsset model (clips, music, final MP4)
│   │   ├── schemas/                  # Pydantic v2 validation models
│   │   │   ├── auth.py               # UserRegister, UserLogin, UserResponse
│   │   │   ├── project.py            # ProjectCreate, ProjectUpdate, ProjectResponse
│   │   │   ├── image.py              # ImageResponse
│   │   │   ├── scene.py              # Qwen3.6-27B ScenePlan & ScenePromptItem
│   │   │   └── job.py                # JobStage, JobStatus, JobResponse
│   │   ├── ai/                       # AI provider adapters (Groq, fal.ai video & music)
│   │   ├── tasks/                    # Celery asynchronous workflow tasks
│   │   ├── composer/                 # FFmpeg composition pipeline
│   │   └── main.py                   # FastAPI application factory & lifespan
│   ├── tests/                        # Automated test suite
│   │   ├── conftest.py               # Test fixtures & test DB configuration
│   │   ├── test_api.py               # Endpoint verification tests
│   │   └── test_raw.py               # Core ORM and session test suite
│   ├── requirements.txt              # Backend dependencies
│   ├── alembic.ini                   # Alembic configuration
│   └── .env.example                  # Backend environment template
├── frontend/
│   ├── src/
│   │   └── app/
│   │       ├── layout.tsx            # Root layout with dark mode & styling
│   │       ├── globals.css           # Tailwind CSS
│   │       └── page.tsx              # Modern landing page & feature overview
│   ├── package.json                  # Next.js, React, Tailwind CSS
│   └── tsconfig.json                 # TypeScript configuration
└── .env.example                      # Root environment configuration
```

---

## AI Generation Pipeline

1. **Image Understanding**: Groq hosted API running `Qwen3.6-27B` multimodal model parses uploaded reference images and user intent to generate a structured JSON scene plan.
2. **Scene Planning**: Dynamically splits the duration into 5-second scenes (15s = 3 scenes, 20s = 4 scenes, 30s = 6 scenes).
3. **Clip Generation**: fal.ai hosted `Wan 2.2 5B` image-to-video API animates each reference photo with motion prompts.
4. **Music Generation**: fal.ai hosted `ACE-Step` API synthesizes an instrumental track tailored to the requested mood and video length.
5. **Video Composition**: FFmpeg normalizes, concatenates clips, overlays text, transitions, and mixes audio into a final MP4 under 30 seconds.

---

## Phase 1 Implementation Summary

- **Backend Foundation**: Complete FastAPI application architecture with modular layers (core, models, schemas, api, services).
- **PostgreSQL Database Integration**: SQLAlchemy 2.0 ORM models for `User`, `Session`, `VideoProject`, `ProjectImage`, `GenerationJob`, and `GeneratedAsset`.
- **PostgreSQL JSONB**: Configured native JSONB column support for scene plans, external provider IDs, and asset metadata.
- **Alembic Migrations**: Fully configured with `0001_initial_schema.py` generating native PostgreSQL DDL with version tracking.
- **Server-Side Session Authentication**: Implemented secure, random session tokens stored in PostgreSQL with HttpOnly cookies, SameSite protection, and expiration handling (no localStorage).
- **Frontend Scaffolding**: Initialized Next.js 16 with TypeScript, Tailwind CSS, responsive design, and modern dark-mode landing page.
- **Verified Health & Lifecycle**: Tested server startup, database table generation, user registration, login/logout, and project lifecycle with 0 errors.

---

## Quickstart & Execution

### 1. Backend Setup

```bash
cd backend

# Create and activate virtual environment
python -m venv venv
.\venv\Scripts\activate       # On Windows
# source venv/bin/activate    # On Linux/macOS

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
```

### 2. Configure PostgreSQL

Update `DATABASE_URL` and `SYNC_DATABASE_URL` in `backend/.env`:

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/video_gen_db
SYNC_DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/video_gen_db
```

Run database migrations:

```bash
alembic upgrade head
```

### 3. Start the Backend Server

```bash
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Interactive API documentation will be available at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- Health check: `http://localhost:8000/api/v1/health`

### 4. Run Backend Tests

```bash
cd backend
python tests/test_raw.py
```

### 5. Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Frontend will run at `http://localhost:3000`.
