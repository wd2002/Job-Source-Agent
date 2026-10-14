from __future__ import annotations

import json
import multiprocessing
import os
import re
from collections import deque
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .models import JobSourceResult

USER_AGENT = "JobSourceAgent/1.0 (+https://github.com/wd2002/Job-Source-Agent)"
CAREER_TERMS = (
    "career", "careers", "jobs", "join-us", "join our team", "work-with-us",
    "opportunities", "open-role", "open-roles", "openings", "job-list",
)
ATS_HOSTS = {
    "ashbyhq.com": "Ashby",
    "greenhouse.io": "Greenhouse",
    "lever.co": "Lever",
    "workable.com": "Workable",
    "smartrecruiters.com": "SmartRecruiters",
    "teamtailor.com": "Teamtailor",
    "myworkdayjobs.com": "Workday",
}
URL_PATTERN = re.compile(
    r"https?://[^\s\"'<>\\]+"
)
NON_WEBSITE_HOSTS = {
    "bing.com",
    "facebook.com",
    "google.com",
    "instagram.com",
    "linkedin.com",
    "mapquest.com",
    "twitter.com",
    "x.com",
    "youtube.com",
}


@dataclass
class Page:
    url: str
    html: str


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _is_http_url(url: str | None) -> bool:
    return bool(url and urlparse(url).scheme in {"http", "https"} and urlparse(url).netloc)


def _is_linkedin_url(url: str | None) -> bool:
    return _host(url or "") == "linkedin.com" or _host(url or "").endswith(".linkedin.com")


def _is_external_website(url: str | None) -> bool:
    return _is_http_url(url) and _host(url or "") not in NON_WEBSITE_HOSTS


def _is_ats(url: str) -> str | None:
    host = _host(url)
    for domain, provider in ATS_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return provider
    return None


def _ats_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Find ATS links in anchors, embeds, structured data, and JS bundles."""
    soup = BeautifulSoup(html, "html.parser")
    raw_values: list[str] = []
    for tag in soup.find_all(True):
        for attribute in ("href", "src", "content", "action", "data-href", "data-url", "data-src"):
            value = tag.get(attribute)
            if value:
                raw_values.append(value)
    raw_values.extend(script.get_text(" ", strip=True) for script in soup.find_all("script"))
    raw_values.append(html)

    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in raw_values:
        normalised_raw = raw.replace("\\/", "/")
        candidates = [normalised_raw] + URL_PATTERN.findall(normalised_raw)
        for candidate in candidates:
            candidate = candidate.replace("\\/", "/").replace("&amp;", "&").rstrip("),.;")
            target = urljoin(base_url, candidate)
            provider = _is_ats(target)
            if provider and target not in seen:
                seen.add(target)
                results.append((target, provider))
    return results


def _job_links(html: str, base_url: str, title: str | None, location: str | None) -> list[str]:
    """Extract company-hosted job detail links and rank them against the LinkedIn job."""
    if not title:
        return []
    wanted = set(re.findall(r"[a-z0-9]+", title.lower()))
    location_words = set(re.findall(r"[a-z0-9]+", (location or "").lower()))
    soup = BeautifulSoup(html, "html.parser")
    ranked: list[tuple[int, str]] = []
    for anchor in soup.find_all("a", href=True):
        target = urljoin(base_url, anchor["href"])
        if not _is_http_url(target):
            continue
        text = " ".join(anchor.get_text(" ", strip=True).split())
        parent = anchor.find_parent(["li", "article", "div", "tr"])
        context = " ".join(parent.get_text(" ", strip=True).split()) if parent else text
        words = set(re.findall(r"[a-z0-9]+", context.lower()))
        title_score = len(wanted & words)
        location_score = len(location_words & words)
        path_hint = any(term in target.lower() for term in ("/career", "/job", "/role", "/position"))
        if title_score >= max(1, min(3, len(wanted))) and path_hint:
            ranked.append((title_score * 10 + location_score, target))
    return [url for _, url in sorted(ranked, reverse=True)]


def _pagination_links(html: str, base_url: str) -> list[str]:
    """Find same-site pagination links for server-rendered or hydrated job lists."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        target = urljoin(base_url, anchor["href"]).split("#", 1)[0]
        if not _is_http_url(target) or _host(target) != _host(base_url) or target in seen:
            continue
        text = " ".join(anchor.get_text(" ", strip=True).lower().split())
        rel = " ".join(anchor.get("rel", [])).lower()
        aria_label = anchor.get("aria-label", "").lower()
        query = parse_qs(urlparse(target).query)
        is_page_query = any(key in query for key in ("page", "p", "pageNumber", "page_index"))
        is_navigation = any(term in f"{text} {rel} {aria_label}" for term in ("next", "previous", "prev", "first", "last"))
        if is_page_query or is_navigation:
            seen.add(target)
            results.append(target)
    return results


def _career_entry_links(html: str, base_url: str) -> list[str]:
    """Find dedicated job-list sections such as /careers#open-roles."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        target = urljoin(base_url, anchor["href"])
        if not _is_http_url(target) or _host(target) != _host(base_url):
            continue
        text = " ".join(anchor.get_text(" ", strip=True).lower().split())
        parsed = urlparse(target)
        target_text = f"{parsed.path} {parsed.fragment} {text}".lower()
        if not parsed.fragment and not any(term in target_text for term in CAREER_TERMS):
            continue
        if any(term in target_text for term in CAREER_TERMS) and target not in seen:
            seen.add(target)
            results.append(target)
    return results


def _parse_linkedin_title(raw_title: str | None, company_name: str | None) -> tuple[str | None, str | None]:
    if not raw_title:
        return None, None
    title = re.sub(r"\s*\|\s*LinkedIn(?:\s+Jobs?)?\s*$", "", raw_title, flags=re.I).strip()
    company = re.escape(company_name or "")
    if company:
        location_separator = r"(?:\s+in\s+|\s+[—–-]\s+)"
        match = re.match(
            rf"(.+?)\s+(?:at|hiring)\s+{company}(?:{location_separator}(.+))?$",
            title,
            re.I,
        )
        if not match:
            match = re.match(
                rf"{company}\s+hiring\s+(.+?)(?:{location_separator}(.+))?$",
                title,
                re.I,
            )
        if match:
            return match.group(1).strip(), match.group(2).strip() if match.group(2) else None
    return title, None


def _linkedin_apply_url(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.find_all(["a", "button", "form"]):
        label = " ".join(element.get("aria-label", "").split()).lower()
        tracking = element.get("data-tracking-control-name", "").lower()
        text = element.get_text(" ", strip=True).lower()
        if "apply" not in label and "apply" not in tracking and "apply" not in text:
            continue
        for attribute in ("href", "action", "data-url", "data-href", "data-apply-url"):
            value = element.get(attribute)
            target = urljoin("https://www.linkedin.com", value) if value else ""
            if _is_external_website(target) and not _is_linkedin_url(target):
                return target
    return None


def _normalise_website(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value.rstrip("/")


class JobSourceResolver:
    def __init__(self, client: httpx.AsyncClient | None = None, max_pages: int = 8):
        self.client = client
        self.max_pages = max_pages

    async def resolve(self, linkedin_url: str) -> JobSourceResult:
        result = JobSourceResult(linkedin_url=linkedin_url)
        try:
            linkedin_page = await self._get(linkedin_url)
            metadata = self._linkedin_metadata(linkedin_page.html)
            company_website = metadata.get("company_website")
            if _is_linkedin_url(company_website):
                company_website = await self._external_company_website(company_website)
            metadata["company_website"] = company_website or ""
            if not company_website:
                metadata.update(await self._crawler_metadata(linkedin_url))
            result.company_name = metadata.get("company_name")
            result.company_website = _normalise_website(metadata.get("company_website"))
            raw_title = metadata.get("title")
            if not result.company_name and raw_title:
                title_match = re.search(r"\bat\s+([\w.-]+)", raw_title, re.I)
                if not title_match:
                    title_match = re.search(r"^([\w.-]+)\s+hiring\b", raw_title, re.I)
                if title_match:
                    result.company_name = title_match.group(1)
            result.title, result.location = _parse_linkedin_title(raw_title, result.company_name)
            result.apply_url = _linkedin_apply_url(linkedin_page.html)
            result.steps.append("Extracted LinkedIn job metadata")
            if not result.company_website:
                raise ValueError("Company website was not found in LinkedIn metadata")

            if result.apply_url:
                result.jobs_url = result.apply_url
                result.matched_job_url = result.apply_url
                result.provider = _is_ats(result.apply_url)
                result.success = True
                result.confidence = 0.99
                result.steps.append("Used external URL from LinkedIn Apply button")
                return result

            found = await self._find_jobs(result.company_website, result.title, result.location)
            result.careers_url = found.get("careers_url")
            result.jobs_url = found.get("jobs_url")
            result.provider = found.get("provider")
            result.matched_job_url = found.get("matched_job_url")
            result.success = bool(result.jobs_url)
            result.confidence = found.get("confidence", 0.0)
            result.steps.extend(found.get("steps", []))
            if not result.success:
                result.error = "Careers page was reached and rendered, but no job-listing source was detected"
        except (httpx.HTTPError, ValueError) as exc:
            result.error = str(exc)
            result.steps.append("Resolution stopped with an explicit error")
        return result

    async def _get(self, url: str) -> Page:
        if self.client is not None:
            response = await self.client.get(url, follow_redirects=True)
        else:
            async with httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT}, timeout=15, follow_redirects=True
            ) as client:
                response = await client.get(url)
        response.raise_for_status()
        return Page(str(response.url), response.text)

    async def _render(self, url: str, title: str | None = None) -> Page | None:
        """Render a page when its job links are created by client-side JavaScript."""
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_render_worker, args=(url, title, sender))
        process.start()
        sender.close()
        process.join(timeout=120)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            receiver.close()
            return None
        if not receiver.poll(1):
            receiver.close()
            return None
        rendered_url, html = receiver.recv()
        receiver.close()
        if not rendered_url or not html:
            return None
        return Page(rendered_url, html)
    async def _crawler_metadata(self, linkedin_url: str) -> dict[str, str]:
        endpoint = os.getenv("LINKEDIN_CRAWLER_URL")
        if not endpoint:
            return {}
        headers = {"Authorization": f"Bearer {os.getenv('LINKEDIN_CRAWLER_TOKEN', '')}"}
        if self.client is not None:
            response = await self.client.post(endpoint, json={"url": linkedin_url}, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(endpoint, json={"url": linkedin_url}, headers=headers)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("LinkedIn crawler API returned a non-object response")
        return {key: str(data[key]) for key in ("company_name", "company_website", "title") if data.get(key)}

    def _linkedin_metadata(self, html: str) -> dict[str, str]:
        soup = BeautifulSoup(html, "html.parser")
        values: dict[str, str] = {}
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                payload = json.loads(script.string or "")
            except json.JSONDecodeError:
                continue
            records = payload if isinstance(payload, list) else [payload]
            for record in records:
                if not isinstance(record, dict):
                    continue
                values.setdefault("title", str(record.get("title") or record.get("name") or ""))
                hiring = record.get("hiringOrganization")
                if isinstance(hiring, dict):
                    values.setdefault("company_name", str(hiring.get("name") or ""))
                    values.setdefault("company_website", str(hiring.get("sameAs") or hiring.get("url") or ""))
        for prop, key in (("og:title", "title"), ("og:site_name", "company_name")):
            tag = soup.find("meta", attrs={"property": prop})
            if tag and tag.get("content"):
                values.setdefault(key, tag["content"])
        for anchor in soup.find_all("a", href=True):
            href = urljoin("https://www.linkedin.com", anchor["href"])
            if "/company/" in href and _is_http_url(href) and not values.get("company_website"):
                values.setdefault("company_website", href)
        values = {key: value.strip() for key, value in values.items() if value.strip()}
        # LinkedIn can expose the employer's external website in a profile link.
        external = soup.find("a", attrs={"data-tracking-control-name": re.compile("company", re.I)})
        if external and _is_http_url(external.get("href")):
            values["company_website"] = external["href"]
        return values

    async def _external_company_website(self, company_url: str) -> str | None:
        """Resolve a LinkedIn company profile URL to its external website link."""
        try:
            company_page = await self._get(company_url)
        except httpx.HTTPError:
            return None
        soup = BeautifulSoup(company_page.html, "html.parser")
        candidates: list[str] = []
        for anchor in soup.find_all("a", href=True):
            target = urljoin(company_page.url, anchor["href"])
            if _is_linkedin_url(target):
                query_url = parse_qs(urlparse(target).query).get("url", [None])[0]
                if query_url:
                    target = unquote(query_url)
            if not _is_external_website(target):
                continue
            tracking_name = anchor.get("data-tracking-control-name", "")
            text = anchor.get_text(" ", strip=True).lower()
            if "website" in tracking_name.lower() or "website" in text or "company_website" in tracking_name.lower():
                candidates.insert(0, target)
        for meta in soup.find_all("meta", attrs={"content": True}):
            content = meta["content"]
            if _is_external_website(content):
                candidates.append(content)
        return candidates[0] if candidates else None

    async def _find_jobs(self, website: str, title: str | None = None, location: str | None = None) -> dict:
        queue = deque([website, urljoin(website + "/", "careers"), urljoin(website + "/", "jobs")])
        visited: set[str] = set()
        same_host = _host(website)
        careers_url = None
        steps: list[str] = []
        while queue and len(visited) < self.max_pages:
            entry_url = queue.popleft().rstrip("/")
            current = entry_url.split("#", 1)[0]
            if current in visited or not _is_http_url(current) or _host(current) != same_host:
                continue
            visited.add(current)
            try:
                page = await self._get(current)
            except httpx.HTTPError:
                continue
            soup = BeautifulSoup(page.html, "html.parser")
            matching_jobs = _job_links(page.html, page.url, title, location)
            if matching_jobs:
                steps.append("Matched LinkedIn job to a company job detail page")
                return {"careers_url": careers_url or page.url, "jobs_url": page.url,
                        "matched_job_url": matching_jobs[0], "confidence": 0.92, "steps": steps}
            ats_links = _ats_links(page.html, page.url)
            if ats_links:
                target, provider = ats_links[0]
                steps.append(f"Detected {provider} job source in page data")
                return {"careers_url": careers_url or page.url, "jobs_url": target, "provider": provider,
                        "confidence": 0.96, "steps": steps}
            for anchor in soup.find_all("a", href=True):
                target = urljoin(page.url, anchor["href"])
                text = " ".join(anchor.get_text(" ", strip=True).lower().split())
                provider = _is_ats(target)
                if provider:
                    steps.append(f"Detected {provider} job source")
                    return {"careers_url": careers_url or page.url, "jobs_url": target, "provider": provider,
                            "confidence": 0.98, "steps": steps}
                if _is_http_url(target) and _host(target) == same_host:
                    if any(term in (text + " " + target.lower()) for term in CAREER_TERMS):
                        careers_url = careers_url or target
                        queue.append(target)
            if any(term in current.lower() for term in CAREER_TERMS):
                careers_url = careers_url or page.url
                steps.append("Found company careers page")
                for link in soup.find_all("a", href=True):
                    target = urljoin(page.url, link["href"])
                    if _is_ats(target):
                        return {"careers_url": page.url, "jobs_url": target, "provider": _is_ats(target),
                                "confidence": 0.98, "steps": steps}
                render_urls = [entry_url]
                render_urls.extend(_career_entry_links(page.html, page.url))
                for render_url in dict.fromkeys(render_urls):
                    rendered = await self._render(render_url, title)
                    if rendered:
                        steps.append("Rendered careers page with browser")
                        matching_jobs = _job_links(rendered.html, rendered.url, title, location)
                        if matching_jobs:
                            steps.append("Matched LinkedIn job after browser rendering")
                            return {"careers_url": rendered.url, "jobs_url": rendered.url,
                                    "matched_job_url": matching_jobs[0], "confidence": 0.92, "steps": steps}
                        rendered_ats = _ats_links(rendered.html, rendered.url)
                        if rendered_ats:
                            target, provider = rendered_ats[0]
                            steps.append(f"Detected {provider} job source after JavaScript rendering")
                            return {"careers_url": rendered.url, "jobs_url": target, "provider": provider,
                                    "confidence": 0.94, "steps": steps}
        return {"careers_url": careers_url, "confidence": 0.0 if not careers_url else 0.45, "steps": steps}


def _render_worker(url: str, title: str | None, output: multiprocessing.connection.Connection) -> None:
    """Run Playwright outside the ASGI process and its Windows event loop."""
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                initial_url = url
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if title:
                    search = page.locator("input[placeholder*='Search'], input[type='search']").first
                    if search.count():
                        search.fill(title)
                        search.press("Enter")
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except PlaywrightTimeoutError:
                    pass
                page.wait_for_timeout(1200)
                pages = [page.content()]
                visited = {page.url.split("#", 1)[0]}
                pending = _pagination_links(pages[0], page.url)
                for _ in range(40):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(250)
                    next_button = page.locator(
                        "button:has-text('Next'), a:has-text('Next'), "
                        "button:has-text('Load more'), a:has-text('Load more'), "
                        "[aria-label*='next' i], [aria-label*='load more' i]"
                    ).last
                    if not next_button.count() or not next_button.is_visible():
                        break
                    try:
                        next_button.click(timeout=1000)
                    except PlaywrightTimeoutError:
                        next_button = None
                    if next_button is not None:
                        page.wait_for_timeout(1000)
                        content = page.content()
                        pages.append(content)
                        pending.extend(
                            candidate for candidate in _pagination_links(content, page.url)
                            if candidate not in pending
                        )
                        continue
                    target = next((candidate for candidate in pending if candidate not in visited), None)
                    if not target:
                        break
                    visited.add(target)
                    page.goto(target, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(500)
                    content = page.content()
                    pages.append(content)
                    pending.extend(
                        candidate for candidate in _pagination_links(content, page.url)
                        if candidate not in pending
                    )
                output.send((initial_url, "\n".join(pages)))
            finally:
                browser.close()
    except Exception:
        output.send((None, None))
    finally:
        output.close()
