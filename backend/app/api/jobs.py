"""Job search and application API endpoints (spec §J)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, get_db
from app.errors import NotFoundError
from app.models.user import User
from app.tools.job_tools import (
    search_jobs as search_jobs_tool,
    list_user_jobs as list_user_jobs_tool,
    save_job_posting as save_job_posting_tool,
    list_qualifications as list_qualifications_tool,
    add_qualification as add_qualification_tool,
    create_cv_template as create_cv_template_tool,
    generate_cv_for_job as generate_cv_for_job_tool,
    list_application_drafts as list_application_drafts_tool,
    list_applications as list_applications_tool,
    create_application_draft as create_application_draft_tool,
    send_application as send_application_tool,
    review_application_draft as review_application_draft_tool,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


# --- Request/Response Schemas ---

class JobSearchRequest(BaseModel):
    keywords: str = Field(..., description="Job search keywords")
    location: str = Field("", description="Location filter")
    remote_only: bool = Field(False, description="Filter for remote-only positions")
    max_results: int = Field(10, ge=1, le=20, description="Maximum results to return")
    source: str = Field("all", description="Job board source")


class JobPostingOut(BaseModel):
    id: str
    title: str
    company: str
    url: str
    location: str
    is_remote: bool


class JobSearchOut(BaseModel):
    jobs: list[JobPostingOut]
    count: int
    query: str


class SaveJobRequest(BaseModel):
    job_id: str
    title: str
    company: str
    url: str
    location: str = ""
    is_remote: bool = False


class QualificationOut(BaseModel):
    id: str
    category: str
    title: str
    description: str = ""
    years: int | None = None
    is_verified: bool = False
    priority: int


class AddQualificationRequest(BaseModel):
    category: str
    title: str
    description: str = ""
    years: int | None = None
    priority: int = 0


class CVTemplateOut(BaseModel):
    id: str
    name: str
    is_default: bool
    created_at: str


class CreateCVRequest(BaseModel):
    name: str = "My CV"
    content: str = ""
    is_default: bool = False


class GenerateCVRequest(BaseModel):
    job_id: str
    template_id: str | None = None
    include_cover_letter: bool = False


class ApplicationDraftOut(BaseModel):
    id: str
    job_id: str
    job_title: str
    company: str
    subject: str
    status: str
    created_at: str


class ApplicationOut(BaseModel):
    id: str
    job_id: str
    company: str
    position: str
    status: str
    email_sent_to: str
    application_date: str


class CreateDraftRequest(BaseModel):
    job_id: str
    subject: str = ""
    body: str = ""
    cv_content: str = ""


class SendApplicationRequest(BaseModel):
    draft_id: str
    recipient_email: str


# --- Job Search Endpoints ---

@router.post("/search", response_model=JobSearchOut)
async def search_jobs(request: JobSearchRequest, user: User = Depends(get_current_user)):
    """Search for job postings based on criteria."""
    result = await search_jobs_tool(
        keywords=request.keywords,
        location=request.location,
        remote_only=request.remote_only,
        max_results=request.max_results,
        source=request.source,
    )
    if not result.success:
        from app.errors import JaloraError
        raise JaloraError(400, result.error.code if result.error else "ERROR", result.message)

    jobs = [JobPostingOut(**j) for j in result.data.get("jobs", [])]
    return JobSearchOut(
        jobs=jobs,
        count=result.data.get("count", 0),
        query=result.data.get("query", ""),
    )


@router.get("/jobs", response_model=dict)
async def list_jobs(user: User = Depends(get_current_user), limit: int = 20):
    """List saved job postings for the user."""
    result = await list_user_jobs_tool(limit=limit)
    if not result.success:
        from app.errors import JaloraError
        raise JaloraError(400, result.error.code if result.error else "ERROR", result.message)

    return {
        "jobs": result.data.get("jobs", []),
        "count": result.data.get("count", 0),
    }


@router.post("/save", response_model=dict)
async def save_job(request: SaveJobRequest, user: User = Depends(get_current_user)):
    """Save a job posting for later reference."""
    result = await save_job_posting_tool(
        job_id=request.job_id,
        title=request.title,
        company=request.company,
        url=request.url,
        location=request.location,
        is_remote=request.is_remote,
    )
    if not result.success:
        from app.errors import JaloraError
        raise JaloraError(400, result.error.code if result.error else "ERROR", result.message)

    return result.data


# --- Qualification Management Endpoints ---

@router.get("/qualifications", response_model=dict)
async def get_qualifications(user: User = Depends(get_current_user)):
    """List the user's stored qualifications."""
    result = await list_qualifications_tool()
    if not result.success:
        from app.errors import JaloraError
        raise JaloraError(400, result.error.code if result.error else "ERROR", result.message)

    return {
        "qualifications": result.data.get("qualifications", []),
        "count": result.data.get("count", 0),
    }


@router.post("/qualifications", response_model=QualificationOut)
async def add_qualification(request: AddQualificationRequest, user: User = Depends(get_current_user)):
    """Add a qualification to the user's profile."""
    result = await add_qualification_tool(
        category=request.category,
        title=request.title,
        description=request.description,
        years=request.years,
        priority=request.priority,
    )
    if not result.success:
        from app.errors import JaloraError
        raise JaloraError(400, result.error.code if result.error else "ERROR", result.message)

    return QualificationOut(**result.data)


# --- CV Management Endpoints ---

@router.post("/cv", response_model=dict)
async def create_cv(request: CreateCVRequest, user: User = Depends(get_current_user)):
    """Create or update a CV template."""
    result = await create_cv_template_tool(
        name=request.name,
        content=request.content,
        is_default=request.is_default,
    )
    if not result.success:
        from app.errors import JaloraError
        raise JaloraError(400, result.error.code if result.error else "ERROR", result.message)

    return result.data


@router.post("/cv/generate", response_model=dict)
async def generate_cv(request: GenerateCVRequest, user: User = Depends(get_current_user)):
    """Generate a CV tailored to a specific job."""
    result = await generate_cv_for_job(
        job_id=request.job_id,
        template_id=request.template_id,
        include_cover_letter=request.include_cover_letter,
    )
    if not result.success:
        from app.errors import JaloraError
        raise JaloraError(400, result.error.code if result.error else "ERROR", result.message)

    return result.data


# --- Application Draft Endpoints ---

@router.get("/drafts", response_model=dict)
async def list_drafts(user: User = Depends(get_current_user)):
    """List all application drafts."""
    result = await list_application_drafts_tool()
    if not result.success:
        from app.errors import JaloraError
        raise JaloraError(400, result.error.code if result.error else "ERROR", result.message)

    return {
        "drafts": result.data.get("drafts", []),
        "count": result.data.get("count", 0),
    }


@router.get("/drafts/{draft_id}", response_model=dict)
async def review_draft(draft_id: str, user: User = Depends(get_current_user)):
    """Review an application draft."""
    result = await review_application_draft_tool(draft_id=draft_id)
    if not result.success:
        from app.errors import JaloraError
        raise JaloraError(400, result.error.code if result.error else "ERROR", result.message)

    return result.data


@router.post("/drafts", response_model=dict)
async def create_draft(request: CreateDraftRequest, user: User = Depends(get_current_user)):
    """Create an application draft."""
    result = await create_application_draft_tool(
        job_id=request.job_id,
        subject=request.subject,
        body=request.body,
        cv_content=request.cv_content,
    )
    if not result.success:
        from app.errors import JaloraError
        raise JaloraError(400, result.error.code if result.error else "ERROR", result.message)

    return result.data


@router.post("/send", response_model=dict)
async def send_application(request: SendApplicationRequest, user: User = Depends(get_current_user)):
    """Send a job application (requires approval)."""
    result = await send_application_tool(
        draft_id=request.draft_id,
        recipient_email=request.recipient_email,
    )
    if not result.success:
        from app.errors import JaloraError
        raise JaloraError(400, result.error.code if result.error else "ERROR", result.message)

    return result.data


# --- Application History Endpoints ---

@router.get("/applications", response_model=dict)
async def list_applications(user: User = Depends(get_current_user)):
    """List all job applications."""
    result = await list_applications_tool()
    if not result.success:
        from app.errors import JaloraError
        raise JaloraError(400, result.error.code if result.error else "ERROR", result.message)

    return {
        "applications": result.data.get("applications", []),
        "count": result.data.get("count", 0),
    }