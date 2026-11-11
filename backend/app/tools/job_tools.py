"""Job search and application tools (spec §J).

Enables users to find jobs, generate tailored CVs, and submit applications
with approval-gated email sending.

Tools:
- search_jobs: Search for job postings based on keywords and location
- get_job_posting: Get details for a specific job posting
- list_user_jobs: List saved job postings
- save_job_posting: Save a job posting for later reference
- create_cv_template: Create or update a CV template
- generate_cv_for_job: Generate a CV tailored to a specific job
- create_application_draft: Draft an application email for a job
- send_application: Send an application email (requires approval)
- list_qualifications: List user's stored qualifications
- add_qualification: Add a qualification/skill to the user's profile
- list_application_drafts: List all application drafts
- list_applications: List all job applications
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app.context import get_tool_context
from app.errors import ToolResult
from app.models.job import JobPosting, JobApplication, CVTemplate, JobQualification, ApplicationDraft
from app.services import search_service
from app.tools.base import PermissionLevel, registry
from app.database.session import SessionLocal


def _user_id() -> str:
    return get_tool_context().user_id


# ============================================================
# Job Search Tools
# ============================================================

async def search_jobs(
    keywords: str = "",
    location: str = "",
    remote_only: bool = False,
    max_results: int = 10,
    source: str = "all",
) -> ToolResult:
    """Search for job postings based on keywords, location, and other filters.

    Uses web search to find relevant job postings from various sources.
    Results are unverified candidates with a URL, not persisted job records.
    Open each selected vacancy with get_job_posting before using its returned
    persisted ID with CV or application tools.

    Args:
        keywords: Job search keywords (e.g., "software engineer python")
        location: Location filter (e.g., "remote", "New York", "San Francisco")
        remote_only: Whether to filter for remote-only positions
        max_results: Maximum number of jobs to return (default 10, max 20)
        source: Job board source (e.g., "linkedin", "indeed", "glassdoor", "all")

    Returns:
        ToolResult with list of matching job postings
    """
    if not keywords or not keywords.strip():
        return ToolResult.fail(
            "INVALID_INPUT",
            "Keywords are required for job search.",
            message="Please provide job search keywords (e.g., 'software engineer python')."
        )

    max_results = min(max(1, int(max_results)), 20)

    # Build search query
    query_parts = [keywords.strip()]
    if location:
        query_parts.append(location.strip())
    if remote_only:
        query_parts.append("remote")

    query = " ".join(query_parts)

    # Search for jobs
    try:
        results = await search_service.search(query, max_results=max_results * 2)
    except Exception as exc:
        return ToolResult.fail(
            "SEARCH_ERROR",
            f"Job search failed: {exc}",
            message="Failed to search for jobs. Please try again."
        )

    # Process and save results
    jobs = []
    for result in results[:max_results]:
        listing_status = _listing_status(result.url, result.title)
        if listing_status == "search_page":
            # A search landing page is evidence that a search ran, not a
            # vacancy. Keep it out of the active list so later CV generation
            # cannot treat a generic results page as an individual job.
            continue
        job_data = {
            "title": _extract_title(result.title, keywords),
            "company": _extract_company(result.title),
            "url": result.url,
            "snippet": result.snippet,
            "location": location if location else "Remote" if remote_only else "Varies",
            "is_remote": remote_only,
            "listing_status": listing_status,
            "verified": False,
        }
        jobs.append(job_data)

    return ToolResult.ok(
        f"Found {len(jobs)} candidate job result(s); each vacancy must be opened and verified before use.",
        {"jobs": jobs, "count": len(jobs), "query": query}
    )


def _listing_status(url: str, title: str = "") -> str:
    """Classify a search result without claiming vacancy verification.

    Search pages are never accepted as individual listings. Other URLs remain
    candidates until ``get_job_posting`` successfully opens and parses them.
    """
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "inaccessible"
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    query = parse_qs(parsed.query)
    landing_hosts = {"google.com", "www.google.com", "bing.com", "www.bing.com"}
    landing_paths = ("/search", "/jobs/search", "/job-search", "/careers/search")
    if host in landing_hosts or any(marker in path for marker in landing_paths):
        return "search_page"
    if any(key in query for key in ("q", "query", "keywords", "search")) and not any(
        marker in path for marker in ("/job/", "/jobs/", "/vacancy/", "/careers/")
    ):
        return "search_page"
    return "candidate"


def _extract_title(title: str, keywords: str) -> str:
    """Extract a clean job title from search result."""
    # Remove duplicate keywords from title
    title_lower = title.lower()
    keywords_lower = keywords.lower()

    parts = []
    for word in title_lower.split():
        if word not in keywords_lower.split():
            parts.append(word)

    if parts:
        return " ".join(parts).strip().title()
    return title


def _extract_company(title: str) -> str:
    """Extract company name from search result title."""
    lowered = title.lower()
    if " at " in lowered:
        company = title[lowered.index(" at ") + 4:]
        for marker in (" | ", " - ", " – ", " — "):
            company = company.split(marker, 1)[0]
        return company.strip()
    # Simple heuristic: company is often at the end or after dash
    if " - " in title:
        return title.split(" - ")[-1].split(" | ", 1)[0].strip()
    if " | " in title:
        return title.split(" | ")[-1].strip()
    return ""


async def get_job_posting(job_url: str) -> ToolResult:
    """Get detailed information for a specific job posting.

    Fetches and parses the job posting page to extract full details.

    Args:
        job_url: URL of the job posting

    Returns:
        ToolResult with job posting details
    """
    if not job_url or not job_url.strip():
        return ToolResult.fail(
            "INVALID_INPUT",
            "Job URL is required.",
            message="Please provide a valid job posting URL."
        )

    if _listing_status(job_url, "") == "search_page":
        return ToolResult.fail(
            "NOT_INDIVIDUAL_VACANCY",
            "The URL is a search or directory page, not an individual vacancy.",
            message="Open an individual vacancy page before using this job result.",
        )

    try:
        # Import browser tools
        from app.tools import browser as browser_tools

        # Open the job page
        result = await browser_tools.browser_open(job_url)
        if not result.success:
            return result

        # Extract content
        result = await browser_tools.browser_extract("text")
        if not result.success:
            return result

        data = result.data
        text = data.get("text", "")

        # Parse the content for job details
        job_info = _parse_job_details(
            text, job_url, page_title=str(data.get("title") or "")
        )
        if not _has_vacancy_evidence(text, job_info):
            return ToolResult.fail(
                "NOT_INDIVIDUAL_VACANCY",
                "The page does not contain enough evidence of an individual vacancy.",
                message="This page was not saved as a verified vacancy.",
            )
        job_info["verified"] = True
        job_info["verification_status"] = "verified_individual_vacancy"

        # Save to database
        user_id = _user_id()
        with SessionLocal() as db:
            existing = db.query(JobPosting).filter(
                JobPosting.url == job_url,
                JobPosting.user_id == user_id
            ).first()

            if not existing:
                job_posting = JobPosting(
                    user_id=user_id,
                    title=job_info.get("title", ""),
                    company=job_info.get("company", ""),
                    location=job_info.get("location", ""),
                    url=job_url,
                    source=job_info.get("source", "web"),
                    description=job_info.get("description", ""),
                    requirements=job_info.get("requirements", []),
                    qualifications=job_info.get("qualifications", []),
                )
                db.add(job_posting)
                db.commit()
                db.refresh(job_posting)

                job_info["id"] = job_posting.id
                job_info["saved"] = True
            else:
                job_info["id"] = existing.id
                job_info["saved"] = True

        return ToolResult.ok(
            f"Retrieved job: {job_info.get('title', 'Unknown')} at {job_info.get('company', 'Unknown')}.",
            job_info
        )

    except Exception as exc:
        return ToolResult.fail(
            "EXTRACTION_ERROR",
            f"Failed to extract job details: {exc}",
            message="Could not retrieve job posting details. The page may be inaccessible or formatted unexpectedly."
        )


def _parse_job_details(text: str, url: str, page_title: str = "") -> dict:
    """Parse job details from extracted page text."""
    result = {
        "title": "",
        "company": "",
        "location": "",
        "description": text[:5000],  # First 5000 chars as description
        "requirements": [],
        "qualifications": [],
        "source": "web",
    }

    # Extract basic info from first few lines
    lines = [l.strip() for l in text.split("\n") if l.strip()][:20]
    if lines:
        result["title"] = lines[0][:200] if len(lines) > 0 else ""
        if len(lines) > 1:
            company_part = lines[1]
            # Try to extract company
            for marker in ["@", "at ", " | ", " - "]:
                if marker in company_part.lower():
                    parts = company_part.split(marker)
                    if len(parts) > 1:
                        result["company"] = parts[-1].strip()[:200]
                        break
    elif page_title.strip():
        result["title"] = page_title.strip()[:200]

    if not result["company"]:
        for candidate in (page_title, *lines[:5]):
            company = _extract_company(candidate)
            if company:
                result["company"] = company[:200]
                break

    # Extract requirements/qualifications from the text
    req_markers = ["requirements:", "qualifications:", "skills:", "education:"]
    for marker in req_markers:
        if marker.lower() in text.lower():
            start = text.lower().find(marker)
            # Extract ~500 chars after the marker
            end = text.find("\n\n", start) if start >= 0 else start + 500
            if end == -1:
                end = start + 500
            section = text[start:end]
            result["requirements"] = [
                line.strip("- * ").strip()
                for line in section.split("\n")
                if line.strip() and not line.strip().startswith(marker)
            ][:10]

    return result


def _has_vacancy_evidence(text: str, job_info: dict) -> bool:
    """Require vacancy-specific evidence before persisting a job result."""
    lowered = (text or "").lower()
    markers = (
        "responsibilities", "requirements", "qualifications", "job type",
        "employment type", "how to apply", "apply now", "location",
        "salary", "experience", "about the role", "description",
    )
    return bool(
        job_info.get("title")
        and job_info.get("company")
        and sum(marker in lowered for marker in markers) >= 2
    )


async def list_user_jobs(limit: int = 20) -> ToolResult:
    """List saved job postings for the user.

    Args:
        limit: Maximum number of jobs to return (default 20)

    Returns:
        ToolResult with list of saved job postings
    """
    user_id = _user_id()
    limit = min(max(1, int(limit)), 50)

    with SessionLocal() as db:
        jobs = db.query(JobPosting).filter(
            JobPosting.user_id == user_id
        ).order_by(JobPosting.scraped_at.desc()).limit(limit).all()

        job_list = [
            {
                "id": job.id,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "url": job.url,
                "is_remote": job.is_remote,
                "applied": job.applied,
                "scraped_at": job.scraped_at.isoformat() if job.scraped_at else None,
            }
            for job in jobs
        ]

    return ToolResult.ok(
        f"Found {len(job_list)} saved job(s).",
        {"jobs": job_list, "count": len(job_list)}
    )


async def save_job_posting(
    job_id: str,
    title: str,
    company: str,
    url: str,
    location: str = "",
    is_remote: bool = False,
) -> ToolResult:
    """Save a job posting for later reference.

    Args:
        job_id: Identifier for the job
        title: Job title
        company: Company name
        url: Job posting URL
        location: Job location (optional)
        is_remote: Whether the job is remote

    Returns:
        ToolResult with saved job info
    """
    if not url:
        return ToolResult.fail(
            "INVALID_INPUT",
            "Job URL is required.",
            message="Please provide a valid job posting URL."
        )

    user_id = _user_id()

    # Check if already saved
    with SessionLocal() as db:
        existing = db.query(JobPosting).filter(
            JobPosting.url == url,
            JobPosting.user_id == user_id
        ).first()

        if existing:
            return ToolResult.ok(
                "Job already saved.",
                {"id": existing.id, "title": existing.title, "company": existing.company, "url": existing.url, "already_saved": True}
            )

        job_posting = JobPosting(
            user_id=user_id,
            title=title[:255] if title else "Untitled",
            company=company[:255] if company else "Unknown",
            url=url,
            location=location[:255] if location else "",
            is_remote=is_remote,
            source="user_saved",
        )
        db.add(job_posting)
        db.commit()
        db.refresh(job_posting)

    return ToolResult.ok(
        f"Saved job: {job_posting.title} at {job_posting.company}.",
        {
            "id": job_posting.id,
            "title": job_posting.title,
            "company": job_posting.company,
            "url": job_posting.url,
            "saved_at": job_posting.scraped_at.isoformat() if job_posting.scraped_at else None,
        }
    )


# ============================================================
# Qualification Management Tools
# ============================================================

async def list_qualifications() -> ToolResult:
    """List the user's stored qualifications and skills.

    Returns:
        ToolResult with list of qualifications
    """
    user_id = _user_id()

    with SessionLocal() as db:
        quals = db.query(JobQualification).filter(
            JobQualification.user_id == user_id
        ).order_by(JobQualification.priority, JobQualification.created_at.desc()).all()

        qual_list = [
            {
                "id": qual.id,
                "category": qual.category,
                "title": qual.title,
                "description": qual.description,
                "years": qual.years,
                "is_verified": qual.is_verified,
                "priority": qual.priority,
            }
            for qual in quals
        ]

    return ToolResult.ok(
        f"Found {len(qual_list)} qualification(s).",
        {"qualifications": qual_list, "count": len(qual_list)}
    )


async def add_qualification(
    category: str,
    title: str,
    description: str = "",
    years: int | None = None,
    priority: int = 0,
) -> ToolResult:
    """Add a qualification or skill to the user's profile.

    Args:
        category: Category (experience, education, skills, certifications)
        title: Qualification/skill name
        description: Description or details
        years: Years of experience (optional)
        priority: Priority level (0=high, 1=medium, 2=low)

    Returns:
        ToolResult with added qualification info
    """
    valid_categories = ["experience", "education", "skills", "certifications"]
    if category.lower() not in valid_categories:
        return ToolResult.fail(
            "INVALID_CATEGORY",
            f"Category must be one of: {', '.join(valid_categories)}",
            message=f"Please use one of the valid categories: {', '.join(valid_categories)}"
        )

    user_id = _user_id()

    qualification = JobQualification(
        user_id=user_id,
        category=category.lower(),
        title=title[:255] if title else "Untitled",
        description=description[:1000] if description else "",
        years=int(years) if years else None,
        priority=int(priority) if priority in [0, 1, 2] else 0,
    )

    with SessionLocal() as db:
        db.add(qualification)
        db.commit()
        db.refresh(qualification)

    return ToolResult.ok(
        f"Added qualification: {qualification.title}.",
        {
            "id": qualification.id,
            "category": qualification.category,
            "title": qualification.title,
            "description": qualification.description,
            "years": qualification.years,
        }
    )


# ============================================================
# CV Generation Tools
# ============================================================

async def create_cv_template(
    name: str = "My CV",
    content: str = "",
    is_default: bool = False,
) -> ToolResult:
    """Create or update a CV template.

    Args:
        name: Name for the CV template
        content: CV content (markdown or plaintext)
        is_default: Whether to set as default CV

    Returns:
        ToolResult with created/updated CV info
    """
    user_id = _user_id()

    with SessionLocal() as db:
        # If setting as default, unset other defaults
        if is_default:
            db.query(CVTemplate).filter(
                CVTemplate.user_id == user_id,
                CVTemplate.is_default == True  # noqa: E712
            ).update({CVTemplate.is_default: False})

        # Check if template with same name exists
        existing = db.query(CVTemplate).filter(
            CVTemplate.user_id == user_id,
            CVTemplate.name == name
        ).first()

        if existing:
            existing.content = content
            existing.is_default = is_default
            existing.updated_at = existing.updated_at
            db.commit()
            db.refresh(existing)
            cv = existing
            cv_id = cv.id
        else:
            cv = CVTemplate(
                user_id=user_id,
                name=name[:128] if name else "My CV",
                content=content,
                is_default=is_default,
            )
            db.add(cv)
            db.commit()
            db.refresh(cv)
            cv_id = cv.id

    return ToolResult.ok(
        f"Created CV template: {cv.name}.",
        {
            "id": cv.id,
            "name": cv.name,
            "is_default": cv.is_default,
            "created_at": cv.created_at.isoformat() if cv.created_at else None,
        }
    )


async def generate_cv_for_job(
    job_id: str,
    template_id: str | None = None,
    include_cover_letter: bool = False,
) -> ToolResult:
    """Generate a CV tailored to a specific job.

    Args:
        job_id: Persisted job posting ID (from get_job_posting or save_job_posting)
        template_id: Optional CV template ID to use (uses default if not specified)
        include_cover_letter: Whether to also generate a cover letter

    Returns:
        ToolResult with generated CV content
    """
    user_id = _user_id()

    with SessionLocal() as db:
        # Get the job posting
        job = db.query(JobPosting).filter(
            JobPosting.id == job_id,
            JobPosting.user_id == user_id
        ).first()

        if not job:
            return ToolResult.fail(
                "JOB_NOT_FOUND",
                f"No job posting found with ID: {job_id}",
                message=(
                    f"Could not find a saved job with ID: {job_id}. "
                    "Open a search result with get_job_posting or save it with "
                    "save_job_posting, then use the returned persisted job ID."
                )
            )

        # Get the user's qualifications
        quals = db.query(JobQualification).filter(
            JobQualification.user_id == user_id
        ).order_by(JobQualification.priority).all()

        # Get CV template (or default)
        if template_id:
            template = db.query(CVTemplate).filter(
                CVTemplate.id == template_id,
                CVTemplate.user_id == user_id
            ).first()
        else:
            template = db.query(CVTemplate).filter(
                CVTemplate.user_id == user_id,
                CVTemplate.is_default == True  # noqa: E712
            ).first()

    # Build CV content
    cv_content = _build_cv_content(job, quals, template.content if template else "", include_cover_letter)

    if include_cover_letter:
        cover_letter = _build_cover_letter(job, quals, template.content if template else "")
        cv_content["cover_letter"] = cover_letter

    # Save as application draft
    draft = ApplicationDraft(
        user_id=user_id,
        job_posting_id=job_id,
        subject=f"Application for {job.title} - {user_id}",
        body=cv_content.get("cover_letter", ""),
        cv_content=cv_content.get("content", ""),
    )
    with SessionLocal() as db:
        db.add(draft)
        db.commit()
        db.refresh(draft)

    return ToolResult.ok(
        f"Generated CV for {job.title} at {job.company}.",
        {
            "job_id": job_id,
            "job_title": job.title,
            "company": job.company,
            "content": cv_content.get("content", ""),
            "cover_letter": cv_content.get("cover_letter", ""),
            "draft_id": draft.id,
        }
    )


def _build_cv_content(job: JobPosting, quals: list[JobQualification], template: str, include_cover_letter: bool) -> dict:
    """Build CV content based on job and qualifications."""
    # Group qualifications by category
    by_category = {}
    for q in quals:
        if q.category not in by_category:
            by_category[q.category] = []
        by_category[q.category].append(q)

    # Build CV sections
    sections = []

    # Header
    sections.append(f"# Application for {job.title}")
    sections.append(f"Company: {job.company}")
    sections.append("")

    # Experience
    if "experience" in by_category:
        sections.append("## Experience")
        for exp in by_category["experience"]:
            years_str = f" ({exp.years} years)" if exp.years else ""
            sections.append(f"- **{exp.title}**{years_str}")
            if exp.description:
                sections.append(f"  {exp.description}")

    # Education
    if "education" in by_category:
        sections.append("## Education")
        for edu in by_category["education"]:
            sections.append(f"- **{edu.title}**")
            if edu.description:
                sections.append(f"  {edu.description}")

    # Skills
    if "skills" in by_category:
        sections.append("## Skills")
        skills = [s.title for s in by_category["skills"]]
        sections.append(", ".join(skills))

    # Certifications
    if "certifications" in by_category:
        sections.append("## Certifications")
        for cert in by_category["certifications"]:
            sections.append(f"- {cert.title}")
            if cert.description:
                sections.append(f"  {cert.description}")

    # Requirements
    if job.requirements:
        sections.append("## Job Requirements")
        for req in job.requirements:
            sections.append(f"- {req}")

    content = "\n".join(sections)

    result = {"content": content}
    if include_cover_letter:
        result["cover_letter"] = _build_cover_letter(job, quals, template)

    return result


def _build_cover_letter(job: JobPosting, quals: list[JobQualification], template: str) -> str:
    """Build a cover letter tailored to the job."""
    # Group qualifications by category
    by_category = {}
    for q in quals:
        if q.category not in by_category:
            by_category[q.category] = []
        by_category[q.category].append(q)

    # Get top experience
    experience = by_category.get("experience", [])

    letter = []
    letter.append("Dear Hiring Manager,")
    letter.append("")
    letter.append(f"I am writing to express my interest in the {job.title} position at {job.company}.")
    letter.append("")

    if experience:
        letter.append("In my previous roles, I have developed:")
        for exp in experience[:3]:
            letter.append(f"- {exp.title}" + (f" ({exp.years} years experience)" if exp.years else ""))
        letter.append("")

    # Match requirements
    if job.requirements:
        letter.append("I am particularly drawn to this opportunity because:")
        letter.append("")
        for req in job.requirements[:3]:
            letter.append(f"- My background aligns with the {req.lower()}")
        letter.append("")

    letter.append(f"I would welcome the opportunity to discuss how my skills and experience would be a valuable addition to your team.")
    letter.append("")
    letter.append("Thank you for considering my application.")
    letter.append("")
    letter.append("Best regards,")

    return "\n".join(letter)


# ============================================================
# Application Tools
# ============================================================

async def create_application_draft(
    job_id: str,
    subject: str = "",
    body: str = "",
    cv_content: str = "",
) -> ToolResult:
    """Create a draft application for a job.

    Args:
        job_id: ID of the job posting
        subject: Email subject line
        body: Email body content
        cv_content: CV/resume content

    Returns:
        ToolResult with draft info
    """
    user_id = _user_id()

    with SessionLocal() as db:
        # Verify job exists
        job = db.query(JobPosting).filter(
            JobPosting.id == job_id,
            JobPosting.user_id == user_id
        ).first()

        if not job:
            return ToolResult.fail(
                "JOB_NOT_FOUND",
                f"No job posting found with ID: {job_id}",
                message=f"Could not find job with ID: {job_id}"
            )

        draft = ApplicationDraft(
            user_id=user_id,
            job_posting_id=job_id,
            subject=subject or f"Application for {job.title} at {job.company}",
            body=body or "",
            cv_content=cv_content or "",
            status="draft",
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)

    return ToolResult.ok(
        "Created application draft.",
        {
            "draft_id": draft.id,
            "job_id": job_id,
            "subject": draft.subject,
            "status": draft.status,
        }
    )


async def review_application_draft(draft_id: str) -> ToolResult:
    """Review an application draft before sending.

    Args:
        draft_id: ID of the draft to review

    Returns:
        ToolResult with draft contents for review
    """
    user_id = _user_id()

    with SessionLocal() as db:
        draft = db.query(ApplicationDraft).filter(
            ApplicationDraft.id == draft_id,
            ApplicationDraft.user_id == user_id
        ).first()

        if not draft:
            return ToolResult.fail(
                "DRAFT_NOT_FOUND",
                f"No draft found with ID: {draft_id}",
                message=f"Could not find draft with ID: {draft_id}"
            )

        # Get job info
        job = db.query(JobPosting).filter(JobPosting.id == draft.job_posting_id).first()

        return ToolResult.ok(
            f"Draft review for {job.title if job else 'job'} at {job.company if job else 'company'}.",
            {
                "draft_id": draft.id,
                "job": {
                    "id": job.id if job else None,
                    "title": job.title if job else "Unknown",
                    "company": job.company if job else "Unknown",
                    "url": job.url if job else None,
                },
                "subject": draft.subject,
                "body": draft.body,
                "cv_content": draft.cv_content,
                "status": draft.status,
            }
        )


async def send_application(
    draft_id: str,
    recipient_email: str,
) -> ToolResult:
    """Send a job application email (requires approval).

    This tool will pause execution and request user approval before sending
    the email. The draft will be sent to the specified email address.

    Args:
        draft_id: ID of the application draft to send
        recipient_email: Email address of the recipient

    Returns:
        ToolResult with application status
    """
    user_id = _user_id()

    if not recipient_email or "@" not in recipient_email:
        return ToolResult.fail(
            "INVALID_EMAIL",
            "A valid recipient email address is required.",
            message="Please provide a valid email address for the application."
        )

    with SessionLocal() as db:
        # Get the draft
        draft = db.query(ApplicationDraft).filter(
            ApplicationDraft.id == draft_id,
            ApplicationDraft.user_id == user_id
        ).first()

        if not draft:
            return ToolResult.fail(
                "DRAFT_NOT_FOUND",
                f"No draft found with ID: {draft_id}",
                message=f"Could not find draft with ID: {draft_id}"
            )

        # Get job info
        job = db.query(JobPosting).filter(JobPosting.id == draft.job_posting_id).first()
        if not job:
            return ToolResult.fail(
                "JOB_NOT_FOUND",
                "Could not find job posting for this draft.",
                message="The job posting for this draft is no longer available."
            )

        # Prepare email content
        subject = draft.subject or f"Application for {job.title} at {job.company}"
        body = draft.body or f"I am applying for the position of {job.title} at {job.company}. Please find my CV attached."
        if draft.cv_content:
            body = f"{body}\n\n---CV--\n{draft.cv_content}"

    # Use the email tool to send
    from app.tools import email_tools

    result = await email_tools.send_email(
        to=recipient_email,
        subject=subject,
        body=body
    )

    if result.success:
        # Mark draft as sent and create application record
        with SessionLocal() as db:
            draft.status = "sent"
            db.commit()

            # Create application record
            app = JobApplication(
                user_id=user_id,
                job_posting_id=job.id,
                company=job.company,
                position=job.title,
                resume_version="generated",
                cover_letter=draft.body,
                email_sent_to=recipient_email,
                email_message_id=result.data.get("message_id", ""),
            )
            db.add(app)
            db.commit()

            # Mark job as applied
            job.applied = True
            job.applied_at = job.applied_at or __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
            db.commit()

        return ToolResult.ok(
            f"Application sent to {recipient_email}.",
            {
                "application_id": app.id,
                "job_id": job.id,
                "company": job.company,
                "position": job.title,
                "email_sent_to": recipient_email,
            }
        )

    return result


async def list_application_drafts() -> ToolResult:
    """List all application drafts for the user.

    Returns:
        ToolResult with list of drafts
    """
    user_id = _user_id()

    with SessionLocal() as db:
        drafts = db.query(ApplicationDraft).filter(
            ApplicationDraft.user_id == user_id
        ).order_by(ApplicationDraft.created_at.desc()).all()

        draft_list = []
        for d in drafts:
            job = db.query(JobPosting).filter(JobPosting.id == d.job_posting_id).first()
            draft_list.append({
                "id": d.id,
                "job_id": d.job_posting_id,
                "job_title": job.title if job else "Unknown",
                "company": job.company if job else "Unknown",
                "subject": d.subject,
                "status": d.status,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            })

    return ToolResult.ok(
        f"Found {len(draft_list)} draft(s).",
        {"drafts": draft_list, "count": len(draft_list)}
    )


async def list_applications() -> ToolResult:
    """List all job applications for the user.

    Returns:
        ToolResult with list of applications
    """
    user_id = _user_id()

    with SessionLocal() as db:
        apps = db.query(JobApplication).filter(
            JobApplication.user_id == user_id
        ).order_by(JobApplication.application_date.desc()).all()

        app_list = [
            {
                "id": app.id,
                "job_id": app.job_posting_id,
                "company": app.company,
                "position": app.position,
                "status": app.status,
                "email_sent_to": app.email_sent_to,
                "application_date": app.application_date.isoformat() if app.application_date else None,
                "notes": app.notes,
            }
            for app in apps
        ]

    return ToolResult.ok(
        f"Found {len(app_list)} application(s).",
        {"applications": app_list, "count": len(app_list)}
    )


# ============================================================
# Tool Registration
# ============================================================

# Job search tools
registry.register(
    search_jobs,
    permission=PermissionLevel.AUTO,
    category="jobs",
    tags=["job", "search", "role", "position", "internship", "vacancy"],
    verification_tool="get_job_posting",
)
registry.register(get_job_posting, permission=PermissionLevel.AUTO, category="jobs", tags=["job", "browser"])
registry.register(list_user_jobs, permission=PermissionLevel.AUTO, category="jobs", tags=["job", "list"])
registry.register(save_job_posting, permission=PermissionLevel.NOTIFY, category="jobs", tags=["job", "save"])

# Qualification tools
registry.register(list_qualifications, permission=PermissionLevel.AUTO, category="jobs", tags=["qualification", "list"])
registry.register(add_qualification, permission=PermissionLevel.NOTIFY, category="jobs", tags=["qualification", "add"])

# CV tools
registry.register(create_cv_template, permission=PermissionLevel.AUTO, category="jobs", tags=["cv", "generate"])
registry.register(generate_cv_for_job, permission=PermissionLevel.AUTO, category="jobs", tags=["cv", "job"])

# Application tools
registry.register(create_application_draft, permission=PermissionLevel.AUTO, category="jobs", tags=["application", "draft"])
registry.register(review_application_draft, permission=PermissionLevel.AUTO, category="jobs", tags=["application", "review"])
registry.register(send_application, permission=PermissionLevel.CONFIRM, category="jobs", tags=["application", "send"])
registry.register(list_application_drafts, permission=PermissionLevel.AUTO, category="jobs", tags=["application", "list"])
registry.register(list_applications, permission=PermissionLevel.AUTO, category="jobs", tags=["application", "list"])
