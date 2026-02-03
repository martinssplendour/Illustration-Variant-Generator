📘 Project README — System Guide (How to Run the System)
This README documents the actual implemented architecture of the FastAPI application based on the current codebase. All sections below are derived directly from the source files and reflect real behaviour, not assumptions.
1. App Architecture (High-Level Overview)
1.1 Prerequisites
·       Python 3.12 and pip (for local mode).
·       PostgreSQL 16+ (local or Docker).
·       Redis (required for async modes).
·       Docker Desktop (recommended for dev and prod setups).
Local Functional Mode (.env.local)
1) Create and activate a virtual environment, then install dependencies:
·           python -m venv .venv
·           .venv\Scripts\activate
·           pip install -r requirements.txt
2) Ensure Postgres is running and DATABASE_URL is set in .env.local.
3) Load the environment file and initialize the database/styles:
·           PowerShell: $env:DOTENV_FILE=".env.local"
·           Bash: export DOTENV_FILE=.env.local
·           (or rename .env.local to .env)
·           python scripts\init_database.py
4) Run the application:
·           python app.py
5) Open http://127.0.0.1:5001 in your browser.

This allows you to test all functionalities.
Docker Development Mode (.env.development)
1) Fill in .env.development keys.
2) Start the stack:
·           docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
3) Initialize the database and styles (one time):
·           docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm web python scripts/init_database.py
4) Open http://127.0.0.1:5001

Production Mode (.env.production)
1) create your production secrets .env.production.example or continue from.env.production and fill secrets.
2) Start the production stack:
·           docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod up --build -d
3) Run schema migrations:
·           docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm web python scripts/migrate_schema.py
4) Load initial styles (or re-load after updates):
·           docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm web python scripts/init_database.py
5) Verify health: GET /api/health returns {"status":"ok"}.

If none of these integrate perfectly, you can create a customized env file that allows you to select what you want operational at service level and at the app level.
Scaling and Operations
 Queue Isolation
Variation jobs and background removal jobs are routed to different queues (ivg_generate and ivg_bg). This prevents long background removal tasks from blocking variation throughput.
Broker Durability
Redis should run as a durable service with persistence enabled (AOF or RDB), memory limits, and monitoring. The CELERY_RESULT_EXPIRES setting limits result retention to avoid unbounded growth.
 Autoscaling
Process autoscaling can be enabled via CELERY_WORKER_AUTOSCALE_MIN and CELERY_WORKER_AUTOSCALE_MAX (or the Celery --autoscale flag). Infrastructure autoscaling should scale worker replicas based on queue depth and job duration.

Architectural Pattern
The application follows a service‑oriented FastAPI architecture with clear separation between:
API routing
Business logic (services)
Persistence layers
AI provider abstraction
This design enables:
Modular development
Clear dependency flow
Provider‑agnostic AI integration
Easier testing and extension
Integration as a service into the Monolithic app.
Primary characteristics:
FastAPI dependency injection
Router-based endpoint organisation
Central image pipeline orchestration
Postgres-backed persistence

System Stack
Python 3 + FastAPI: async-friendly API layer with clear request handling and good performance.
Uvicorn: production-grade ASGI server for FastAPI.
Jinja2 with templates + vanilla JavaScript: simple UI that works without a separate frontend build.
Celery: reliable background job queue for long-running AI and image tasks.
Redis: fast broker and result backend for Celery jobs.
PostgreSQL (psycopg): durable storage for assets, styles, and generation history.
Rembg: background removal models and utilities.
Google Gemini (google-genai / google-generativeai): image-to-image generation via "Nano Banana".
Docker + Docker Compose: reproducible dev and production environments.
Actual Directory Structure
ivg/
├── app.py
├── app_factory.py
├── celery_app.py
├── config.py
├── docker-compose.yml
├── docker-compose.dev.yml
├── docker-compose.prod.yml
├── Dockerfile
├── logging_config.py
├── paths.py
├── PRD ILLUSTRATION VARIATION SOLUTION.docx
├── PRODUCTION.md
├── requirements.txt
├── tasks.py
├── __init__.py
├── routes/
│   ├── __init__.py
│   ├── api.py
│   ├── utils.py
│   └── web.py
├── services/
│   ├── __init__.py
│   ├── background_removal.py
│   ├── cleanup.py
│   ├── history.py
│   ├── image_assets.py
│   ├── image_pipeline.py
│   ├── styles_postgres.py
│   ├── timing.py
│   └── ai/
│       ├── __init__.py
│       ├── base.py
│       └── nano_banana.py
├── scripts/
│   ├── cleanup_assets.py
│   ├── init_database.py
│   └── migrate_schema.py
├── static/
│   └── css/
│       └── styles.css
├── templates/
│   └── index.html
├── runtime/
├── style_guides/
├── style_images/
├── .env
├── .env.development
├── .env.example
├── .env.local
└── .env.production.example


Runtime Flow 
create_app() loads env config, configures logging, middleware, and services.
Routes are registered (web.py, api.py).
Requests go to services via AppServices (assets, styles, history, pipeline, background removal).
If ASYNC_TASKS_ENABLED=true, long work runs in Celery tasks (tasks.py, Async).
If ASYNC_TASKS_ENABLED=false, work runs inline in the web process(Sync).
Results and history are stored in Postgres and returned to the caller.
Application Layers 
Web UI: web.py + index.html (server-rendered UI).
API Layer: api.py (styles, variations, background removal, jobs, history, images).
Services: AppServices wraps storage + pipeline + history + styles + background removal.
Workers: Celery tasks in tasks.py (ivg.generate_variation, ivg.remove_background).
Storage: Postgres stores images/styles/history. Redis is used for Celery queues/results (when async enabled).
Image Pipeline (Exact Processing Flow)
Implemented in: image_pipeline.py
Input Handling
Accepts uploaded images, previous results, or style reference images.
Temporary files are created via TemporaryDirectory() for processing.
 Background Removal
Implemented in background_removal.py:
Uses rembg models (birefnet-general).
Returns PNG with a transparent background.
Exposed via API (POST /api/remove-background) and UI.
Asset Storage
Implemented in image_assets.py:
Stores uploads/results in Postgres (image_assets table).
Session-scoped retrieval (session_id).
No filesystem storage for persistent assets.
AI Preparation
Image converted to PNG bytes.
Style rules + style reference injected into the prompt (if provided).
Layout hints are computed from the source image.
Inference
Delegated to NanoBananaEditor (nano_banana.py).
Uses Gemini image model; wrapped with timeout, retry/backoff, and circuit breaker.
If AI fails and a prompt was used, the job fails (no fallback to source image).
Output Storage
Temporary outputs are created in a temp directory during processing.
Final images are stored in Postgres (image_assets).
Results are referenced by UUID.
Styles System (As Implemented)
Style Sources
Postgres-backed styles only (required).
Managed in styles_postgres.py.
Loaded via init_database.py from style_guides/ and style_images/.
 Style Structure
A style includes:
Display name
Rules text (from style guide)
Reference image (stored in Postgres)
Style ID (unique)
Optional style_profile JSON
3.3 Style Resolution Flow
Style ID received.
Style loaded from Postgres.
Rules + reference image injected into AI prompt.

AI Providers
Provider Interface
Defined in base.py:
Standard image edit contract.
Allows provider swap.
Nano Banana Provider
Implemented in nano_banana.py:
Gemini image-to-image generation.
Retries + timeout + circuit breaker.
Used for all generations when IMAGE_PROVIDER=nano_banana.

API Reference
Defined in api.py
GET /api/health
GET /api/styles
GET /api/styles/{style_id}/reference
POST /api/variations
GET /api/jobs/{job_id}
GET /api/jobs/{job_id}/stream (SSE)
POST /api/remove-background
GET /api/images/{image_id}
GET /api/history

Storage Model 
Temporary files: created in OS temp directory via TemporaryDirectory() during processing.
Persistent assets: stored in Postgres (image_assets).
History: stored in Postgres (generation_history).
Styles: stored in Postgres (styles).
No storage.py (removed)


Integration Notes (IVG as a Service)
My suggestions and notes for seamless integration
Identity and History
Current behavior: IVG uses a session cookie to scope history and asset access.
 Platform integration: pass a stable user identifier (not a username) and map it to session_id.
Recommended: add middleware that reads X-User-Id (or equivalent) and sets session_id = user_id. Use a composite key like tenant_id:user_id to isolate data.
Stateless Service 
 IVG should be treated as stateless at the API layer.
State lives in Postgres (assets/history/styles) and Redis (Celery queues/results).
Requests must be safe to route to any IVG instance.

Required Environment Variables (Need to be available)
  APP_SECRET_KEY
 GEMINI_API_KEY
 DATABASE_URL
 CELERY_BROKER_URL / CELERY_RESULT_BACKEND
ASYNC_TASKS_ENABLED (true in production)
 IMAGE_PROVIDER, GEMINI_MODEL, GEMINI_MODEL_FAST, FAST_MODE, FAST_REFERENCE_MAX_SIZE

Job Execution
Workers should run separately (Linux + prefork).
 Queues are isolated: ivg_generate and ivg_bg.
  Run separate worker pools or configure concurrency per queue to protect latency.

API Usage (Platform Calls)
 POST /api/variations to start a variation job.
 GET /api/jobs/{job_id}/stream for SSE updates (preferred).
GET /api/jobs/{job_id} as polling fallback.
 POST /api/remove-background to start background removal.

SSE / Gateway Notes
If using SSE through a proxy, disable buffering and allow long-lived connections.
 Ensure timeouts are long enough for image jobs (tens of seconds to minutes) sometimes it takes 2 minutes to get a background removal completed.
Concurrency Expectations
 Apply per-user concurrency limits to prevent saturation.
 Use queue depth + job duration to autoscale worker replicas.

Reliability
External AI calls can return 429/503; retries with backoff are recommended (This is why they were implemented, please keep).
Surface job failures to the platform so it can retry or notify users.

Observability
 Pass a correlation id (X-Request-Id) from the platform to trace requests.
 Include job_id and user_id in logs to link UI actions to worker executions.
 Monitor queue depth, job failures, latency p95, and Redis memory usage.

Health Checks
Use GET /api/health for liveness checks.
 Add readiness checks at the platform level for Postgres and Redis.

Database Initialization (New Machine)
If the service is deployed on a new machine or a new Postgres instance, initialize the database and load the styles from the local folders.
·       Set DATABASE_URL via your env file (e.g., .env.local or .env.production).
·       Run: python scripts/init_database.py
·       This creates the database (if missing), creates the schema, and loads style rules/images from style_guides/ and style_images/.
.env.production.example and  .env.production
I purposefully used  .env.production.example and .env.production. The earlier serves as a safe template showing required variables without secrets, while .env.production holds the actual deployment values and credentials i think should be considered for testing.

Rembg Background Removal (Compliance Notes)
Rembg is used for background removal. It runs inside our infrastructure and does not require sending image data to a third-party service.
How Rembg Works
 Rembg runs on our servers and uses pretrained segmentation models to remove backgrounds.
Model weights are downloaded once and stored locally on the server (no image uploads are sent out).
Image data is processed in memory and returned to the application as a PNG with transparency.

Compliance Guidance (Approved Cloud Platforms)
 AWS and Render are approved platforms for hosting at twinkl which makes this service compliant.
 No image data is stored at rest unless explicitly configured by the platform.
Data Retention
·       If storage is required, use short retention and explicit deletion policies.
·       Prefer in-memory processing for background removal tasks.







