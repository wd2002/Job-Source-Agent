# LinkedIn Job Source Agent

Resolve a public LinkedIn job URL to the company's job-listing source.

The service:

1. Extracts company name and website from LinkedIn JSON-LD, OpenGraph metadata, visible HTML, or an optional crawler API.
2. Finds the company's careers page using sitemap hints, structured links, and bounded same-site crawling.
3. Detects common ATS providers (Ashby, Greenhouse, Lever, Workable, SmartRecruiters, Teamtailor, and Workday) and returns a job-listing URL.

LinkedIn often exposes its own `/company/...` profile URL instead of the employer's
website. The resolver treats that as an intermediate page, extracts the external
website link from the company profile, and only then starts the careers crawl.

If a careers page creates job links with JavaScript, the resolver falls back to
Playwright browser rendering. After installing Python dependencies, install the
browser once with `.\.venv\Scripts\python.exe -m playwright install chromium`.

## Run locally

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

The optional crawler API is configured with:

```text
LINKEDIN_CRAWLER_URL=https://your-provider.example/linkedin
LINKEDIN_CRAWLER_TOKEN=...
```

The provider should accept `{"url": "..."}` and return JSON containing `company_name`,
`company_website`, and optionally `title`.

## API

`POST /api/resolve`

```json
{"linkedin_url":"https://www.linkedin.com/jobs/view/4427787182/"}
```

The response separates the LinkedIn job into `title` and `location`. When a
company site exposes individual job detail links, `jobs_url` is the discovered
company jobs page and `matched_job_url` is the specific matching role. The
browser fallback searches the role field, scrolls the results, and follows
Next/Load more controls or numbered/query-based pagination links before matching
title and location. This supports career pages whose job cards are rendered by
JavaScript and whose pagination is implemented as ordinary links.

If LinkedIn exposes an external URL on the Apply control, the resolver uses it
first and returns it as both `apply_url` and `jobs_url`. Public logged-out
LinkedIn pages may render Apply as a sign-in modal without exposing the
employer URL; in that case `apply_url` remains null and the company-site
fallback is used.

`POST /api/batch` accepts `{"linkedin_urls":[...]}` and returns per-URL results plus
an aggregate success rate. A result is successful when a job-listing page (not just a
company homepage or generic careers page) is found.

## Tests

```powershell
pytest
```

Live LinkedIn testing is intentionally opt-in because LinkedIn may require login or
rate-limit automated requests. Use `python scripts/run_live_batch.py urls.txt` with
20 public URLs to produce a JSON report suitable for the demo.
