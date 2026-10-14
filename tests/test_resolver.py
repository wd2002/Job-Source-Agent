import httpx
import pytest

from app.resolver import (
    JobSourceResolver,
    _job_links,
    _linkedin_apply_url,
    _career_entry_links,
    _pagination_links,
    _parse_linkedin_title,
)


@pytest.mark.asyncio
async def test_resolves_ashby_source_from_linked_in_metadata():
    pages = {
        "https://www.linkedin.com/jobs/view/4427787182/": """
        <script type="application/ld+json">{"title":"Engineer",
        "hiringOrganization":{"name":"Harvey","sameAs":"https://harvey.ai"}}</script>
        """,
        "https://harvey.ai": '<a href="/careers">Careers</a>',
        "https://harvey.ai/careers": '<a href="https://jobs.ashbyhq.com/harvey?utm_source=58AzKxpoq0">View jobs</a>',
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages.get(str(request.url), ""), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result = await JobSourceResolver(client=client).resolve(
            "https://www.linkedin.com/jobs/view/4427787182/"
        )
    assert result.success is True
    assert result.company_name == "Harvey"
    assert result.provider == "Ashby"
    assert result.jobs_url == "https://jobs.ashbyhq.com/harvey?utm_source=58AzKxpoq0"


@pytest.mark.asyncio
async def test_resolves_external_site_when_linkedin_company_url_is_in_job_metadata():
    pages = {
        "https://www.linkedin.com/jobs/view/4427787182/": """
        <script type="application/ld+json">{"title":"CX Data Analyst",
        "hiringOrganization":{"name":"Harvey",
        "sameAs":"https://www.linkedin.com/company/harvey-ai"}}</script>
        """,
        "https://www.linkedin.com/company/harvey-ai": (
            '<a data-tracking-control-name="organization-website" href="https://harvey.ai">Website</a>'
        ),
        "https://harvey.ai": '<a href="/careers">Careers</a>',
        "https://harvey.ai/careers": '<a href="https://jobs.ashbyhq.com/harvey">View jobs</a>',
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages[str(request.url)], request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result = await JobSourceResolver(client=client).resolve(
            "https://www.linkedin.com/jobs/view/4427787182/"
        )
    assert result.success is True
    assert result.company_website == "https://harvey.ai"
    assert result.provider == "Ashby"
    assert result.jobs_url == "https://jobs.ashbyhq.com/harvey"


@pytest.mark.asyncio
async def test_ignores_linkedin_location_map_and_uses_explicit_website_link():
    pages = {
        "https://www.linkedin.com/jobs/view/4427787182/": (
            '<script type="application/ld+json">{"hiringOrganization":'
            '{"name":"Harvey","sameAs":"https://www.linkedin.com/company/harvey-ai"}}</script>'
        ),
        "https://www.linkedin.com/company/harvey-ai": """
            <a data-tracking-control-name="org-locations_url"
               href="https://www.bing.com/maps?where=201+3rd+St">San Francisco</a>
            <a data-tracking-control-name="organization-website"
               href="https://harvey.ai">Website</a>
        """,
        "https://harvey.ai": '<a href="/careers">Careers</a>',
        "https://harvey.ai/careers": '<a href="https://jobs.ashbyhq.com/harvey">Jobs</a>',
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages[str(request.url)], request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result = await JobSourceResolver(client=client).resolve(
            "https://www.linkedin.com/jobs/view/4427787182/"
        )
    assert result.success is True
    assert result.company_website == "https://harvey.ai"
    assert result.jobs_url == "https://jobs.ashbyhq.com/harvey"


@pytest.mark.asyncio
async def test_finds_ats_url_in_dynamic_script_data():
    pages = {
        "https://www.linkedin.com/jobs/view/2/": """
            <script type="application/ld+json">{"title":"Engineer",
            "hiringOrganization":{"name":"Harvey","sameAs":"https://harvey.ai"}}</script>
        """,
        "https://harvey.ai": '<a href="/careers">Careers</a>',
        "https://harvey.ai/careers": """
            <div id="open-roles"></div>
            <script>window.__JOBS__ =
            {"careersUrl":"https:\\/\\/jobs.ashbyhq.com\\/harvey"}</script>
        """,
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages[str(request.url)], request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result = await JobSourceResolver(client=client).resolve("https://www.linkedin.com/jobs/view/2/")
    assert result.success is True
    assert result.provider == "Ashby"
    assert result.jobs_url == "https://jobs.ashbyhq.com/harvey"


@pytest.mark.asyncio
async def test_follows_separate_open_roles_page():
    pages = {
        "https://harvey.ai": '<a href="/career/open-role">Open roles</a>',
        "https://harvey.ai/careers": '<a href="/career/open-role">Open roles</a>',
        "https://harvey.ai/jobs": "",
        "https://harvey.ai/career/open-role": (
            '<a href="/career/open-role/123">CX Data Analyst New York Apply</a>'
        ),
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages.get(str(request.url), ""), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolver = JobSourceResolver(client=client)
        async def no_browser_render(_url: str, _title: str | None = None):
            return None

        resolver._render = no_browser_render
        result = await resolver._find_jobs(
            "https://harvey.ai", "CX Data Analyst", "New York"
        )
    assert result["matched_job_url"] == "https://harvey.ai/career/open-role/123"


@pytest.mark.asyncio
async def test_renders_hash_open_roles_entry():
    pages = {
        "https://harvey.ai": '<a href="/careers">Careers</a>',
        "https://harvey.ai/careers": '<a href="/careers#open-roles">Open roles</a>',
        "https://harvey.ai/jobs": "",
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages.get(str(request.url), ""), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolver = JobSourceResolver(client=client)
        rendered_urls: list[str] = []

        async def render(url: str, _title: str | None = None):
            rendered_urls.append(url)
            if "#open-roles" in url:
                from app.resolver import Page

                return Page(
                    url,
                    '<a href="/career/open-role/123">CX Data Analyst New York Apply</a>',
                )
            return None

        resolver._render = render
        result = await resolver._find_jobs(
            "https://harvey.ai", "CX Data Analyst", "New York"
        )
    assert "https://harvey.ai/careers#open-roles" in rendered_urls
    assert result["matched_job_url"] == "https://harvey.ai/career/open-role/123"


def test_finds_hash_job_list_entry():
    html = '<a href="/careers#open-roles">Open roles</a>'
    assert _career_entry_links(html, "https://harvey.ai/careers") == [
        "https://harvey.ai/careers#open-roles"
    ]


def test_extracts_company_from_at_title():
    assert _parse_linkedin_title(
        "CX Data Analyst at Harvey in New York, NY | LinkedIn Jobs", "Harvey"
    ) == ("CX Data Analyst", "New York, NY")


def test_extracts_hiring_title_and_location():
    assert _parse_linkedin_title(
        "Harvey hiring CX Data Analyst in New York, United States | LinkedIn", "Harvey"
    ) == ("CX Data Analyst", "New York, United States")


def test_extracts_title_and_location_with_dash_separator():
    assert _parse_linkedin_title(
        "CX Data Analyst at Harvey — New York, United States", "Harvey"
    ) == ("CX Data Analyst", "New York, United States")


def test_extracts_title_and_location_with_ascii_dash_separator():
    assert _parse_linkedin_title(
        "CX Data Analyst at Harvey - New York, United States", "Harvey"
    ) == ("CX Data Analyst", "New York, United States")


def test_matches_individual_company_job_link_by_title_and_location():
    html = """
    <a href="/company/careers/4167">GTM Lead System Administrator Remote</a>
    <a href="/company/careers/4427">CX Data Analyst New York, NY Apply</a>
    """
    assert _job_links(
        html, "https://www.harvey.ai/careers", "CX Data Analyst", "New York, NY"
    ) == ["https://www.harvey.ai/company/careers/4427"]


def test_finds_numbered_career_pages_for_client_rendered_job_lists():
    html = """
    <a href="/careers?page=1#company-careers">1</a>
    <a href="/careers?page=3#company-careers">3</a>
    <a aria-label="Next page" href="/careers?page=4#company-careers">Next</a>
    <a href="https://example.com/careers?page=5">Other company</a>
    """
    assert _pagination_links(html, "https://www.harvey.ai/careers") == [
        "https://www.harvey.ai/careers?page=1",
        "https://www.harvey.ai/careers?page=3",
        "https://www.harvey.ai/careers?page=4",
    ]


def test_extracts_external_linkedin_apply_url():
    html = """
    <a data-tracking-control-name="public_jobs_apply-link-offsite"
       href="https://jobs.ashbyhq.com/harvey/cx-data-analyst">Apply</a>
    """
    assert _linkedin_apply_url(html) == "https://jobs.ashbyhq.com/harvey/cx-data-analyst"


@pytest.mark.asyncio
async def test_reports_missing_company_site_without_success_fallback():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><meta property='og:title' content='Job'></html>", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await JobSourceResolver(client=client).resolve("https://www.linkedin.com/jobs/view/1/")
    assert result.success is False
    assert result.error == "Company website was not found in LinkedIn metadata"
