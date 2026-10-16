# Employee Attendance & Analytics API

A REST API built with FastAPI and MongoDB for managing employee records, attendance punch-in/punch-out operations, attendance corrections, and analytics.

## Requirements

- Python 3.10 or newer
- MongoDB 6.0 or newer
- Git

## Local Setup

### 1. Clone the repository

```bash
git clone YOUR_PUBLIC_REPOSITORY_URL
cd candidate_kit
```

Replace `YOUR_PUBLIC_REPOSITORY_URL` with your actual Git repository URL.

### 2. Create and activate a virtual environment

**Windows Git Bash:**

```bash
python -m venv .venv
source .venv/Scripts/activate
```

**Linux/macOS:**

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Copy `.env.example` to `.env` and configure the MongoDB connection:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=employee_attendance
```

Do not commit `.env` or any credentials to Git.

### 5. Load sample data

```bash
python sample_seed.py
```

Ensure MongoDB is running before loading the sample data.

### 6. Start the API

```bash
uvicorn app.main:app --port 8000
```

For development with automatic reload:

```bash
uvicorn app.main:app --port 8000 --reload
```

## API Documentation

Once the application is running, open:

- Swagger UI: http://127.0.0.1:8000/docs
- OpenAPI JSON: http://127.0.0.1:8000/openapi.json

## Main Features

- Employee creation and listing
- Attendance punch-in and punch-out
- Attendance record listing and manual corrections
- Monthly employee attendance analytics
- Department attendance summaries
- Late-comers leaderboard
- Department attendance trends
- Query execution statistics through admin explain endpoints

## Submission Contents

The public repository should include:

- `app/main.py`
- `requirements.txt`
- `REVIEW.md`
- `DECISIONS.md`
- `README.md`

Include other required project files, such as `openapi.yaml`, `DATA_MODEL.md`, `sample_data/`, and `sample_seed.py`, when they are part of the supplied candidate kit.

Do not commit `.env`, secrets, or a Dockerfile.