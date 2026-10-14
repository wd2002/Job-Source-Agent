from pydantic import BaseModel, Field, HttpUrl


class ResolveRequest(BaseModel):
    linkedin_url: HttpUrl


class BatchRequest(BaseModel):
    linkedin_urls: list[HttpUrl]


class JobSourceResult(BaseModel):
    linkedin_url: str
    company_name: str | None = None
    company_website: str | None = None
    careers_url: str | None = None
    jobs_url: str | None = None
    apply_url: str | None = None
    provider: str | None = None
    title: str | None = None
    location: str | None = None
    matched_job_url: str | None = None
    success: bool = False
    confidence: float = 0.0
    steps: list[str] = Field(default_factory=list)
    error: str | None = None


class BatchResult(BaseModel):
    results: list[JobSourceResult]
    successful: int
    total: int
    success_rate: float
