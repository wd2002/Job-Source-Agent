from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .models import BatchRequest, BatchResult, ResolveRequest, JobSourceResult
from .resolver import JobSourceResolver

app = FastAPI(title="LinkedIn Job Source Agent", version="1.0.0")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return """<!doctype html>
<html><head><meta charset="utf-8"><title>LinkedIn Job Source Agent</title>
<style>body{font:16px system-ui;max-width:820px;margin:40px auto;padding:0 20px}
textarea,input,button{font:inherit;padding:10px}textarea{width:100%;height:100px}
button{margin-top:10px;cursor:pointer}.card{margin-top:20px;padding:16px;background:#f4f6f8;border-radius:8px}
pre{white-space:pre-wrap}</style></head><body>
<h1>LinkedIn Job Source Agent</h1>
<p>Paste a public LinkedIn job URL to find the employer's job-listing source.</p>
<textarea id="url" placeholder="https://www.linkedin.com/jobs/view/4427787182/"></textarea><br>
<button onclick="resolve()">Resolve job source</button><div id="result"></div>
<script>
async function resolve(){const url=document.querySelector('#url').value.trim();
const box=document.querySelector('#result');box.innerHTML='<div class="card">Resolving...</div>';
const r=await fetch('/api/resolve',{method:'POST',headers:{'content-type':'application/json'},
body:JSON.stringify({linkedin_url:url})});const d=await r.json();
box.innerHTML='<div class="card"><pre>'+JSON.stringify(d,null,2).replaceAll('&','&amp;').replaceAll('<','&lt;')+'</pre></div>';}
</script></body></html>"""


@app.post("/api/resolve", response_model=JobSourceResult)
async def resolve(request: ResolveRequest) -> JobSourceResult:
    return await JobSourceResolver().resolve(str(request.linkedin_url))


@app.post("/api/batch", response_model=BatchResult)
async def batch(request: BatchRequest) -> BatchResult:
    results = [await JobSourceResolver().resolve(str(url)) for url in request.linkedin_urls]
    successful = sum(result.success for result in results)
    return BatchResult(results=results, successful=successful, total=len(results),
                       success_rate=successful / len(results) if results else 0.0)
