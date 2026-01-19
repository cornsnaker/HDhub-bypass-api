"""
HDHub Bypass API
================

Endpoints:
  POST /scrape   - Extract all download links from a movie/series page
  POST /bypass   - Resolve a gadgetsweb link to final direct download URL
  POST /bypass_all - Scrape and bypass all links

Usage:
  uvicorn api:app --reload --port 8000
"""

import re
import json
import base64
import asyncio
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
from curl_cffi import requests as curl_requests

# =====================
# MODELS
# =====================

class ScrapeRequest(BaseModel):
    url: str

class BypassRequest(BaseModel):
    url: str

class DownloadLink(BaseModel):
    title: str
    quality: str
    size: str
    type: str
    season: Optional[str] = None
    episode: Optional[str] = None
    links: dict

class ScrapeResponse(BaseModel):
    title: str
    type: str
    batch: List[DownloadLink]
    singles: List[DownloadLink]

class BypassResponse(BaseModel):
    original_url: str
    final_url: str
    filename: Optional[str] = None

# =====================
# CORE BYPASS LOGIC
# =====================

class HDHubBypass:
    def __init__(self):
        self.std_session = requests.Session()
        self.std_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.curl_session = None

    def _get_curl_session(self):
        if not self.curl_session:
            self.curl_session = curl_requests.Session()
            self.curl_session.impersonate = "chrome110"
        return self.curl_session

    def _get(self, url, timeout=15):
        try:
            resp = self.std_session.get(url, timeout=timeout)
            if resp.status_code in [403, 503]:
                raise Exception("CF Block")
            return resp
        except:
            return self._get_curl_session().get(url, timeout=30)

    def rot13(self, s):
        res = []
        for c in s:
            if 'a' <= c <= 'z':
                res.append(chr((ord(c) - ord('a') + 13) % 26 + ord('a')))
            elif 'A' <= c <= 'Z':
                res.append(chr((ord(c) - ord('A') + 13) % 26 + ord('A')))
            else:
                res.append(c)
        return "".join(res)

    def bypass_gadgetsweb(self, url):
        result = {"original_url": url, "final_url": None, "filename": None}

        try:
            resp = self._get(url)

            token_match = re.search(r"s\('o','([^']+)'", resp.text)
            if not token_match:
                raise Exception("Token not found")

            token = token_match.group(1)
            s1 = base64.b64decode(token).decode('utf-8')
            s2 = base64.b64decode(s1).decode('utf-8')
            s3 = self.rot13(s2)
            padded = s3 + "=" * ((4 - len(s3) % 4) % 4)
            s4 = base64.b64decode(padded).decode('utf-8')
            data = json.loads(s4)

            hubcloud_b64 = data.get('o')
            if not hubcloud_b64:
                raise Exception("HubCloud URL missing")

            hubcloud_url = base64.b64decode(hubcloud_b64).decode('utf-8')
            resp = self._get(hubcloud_url)

            title_m = re.search(r'<title>([^<]+)</title>', resp.text)
            if title_m:
                result["filename"] = title_m.group(1).strip()

            btn = re.search(r'id="download"[^>]*href="([^"]+)"', resp.text)
            if not btn:
                btn = re.search(r'href="([^"]+)"[^>]*id="download"', resp.text)
            if not btn:
                raise Exception("Download button not found")

            gamer_url = btn.group(1).replace("&amp;", "&")
            resp = self._get(gamer_url)

            final = re.search(r'href="([^"]+)"[^>]*id="fsl"', resp.text)
            if not final:
                final = re.search(r'id="fsl"[^>]*href="([^"]+)"', resp.text)

            if final:
                result["final_url"] = final.group(1)
            else:
                raise Exception("Final link not found")

        except Exception as e:
            result["error"] = str(e)

        return result

# =====================
# SCRAPER (Fixed with split-based extraction)
# =====================

class HDHubScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

    def scrape_page(self, url):
        resp = self.session.get(url, timeout=30)
        html = resp.text

        is_series = 'series-page.js' in html or 'id="complete-pack"' in html

        title_m = re.search(r'class="page-title"[^>]*>([^<]+)', html)
        title = title_m.group(1).strip() if title_m else "Unknown"

        result = {
            "title": title,
            "type": "series" if is_series else "movie",
            "batch": [],
            "singles": []
        }

        # Extract batch (complete-pack section)
        batch_m = re.search(r'id="complete-pack"(.*?)(?:id="episodes"|</main>)', html, re.DOTALL)
        if batch_m:
            result["batch"] = self._extract(batch_m.group(1), "batch")

        # Extract singles (episodes section)
        singles_m = re.search(r'id="episodes"(.*?)(?:</main>)', html, re.DOTALL)
        if singles_m:
            result["singles"] = self._extract(singles_m.group(1), "single")

        # Movies - extract from whole page
        if not is_series:
            result["batch"] = self._extract(html, "batch")

        return result

    def _extract(self, html, link_type):
        items = []

        # Split on download-item class
        parts = html.split('class="download-item')

        for part in parts[1:]:
            item = {
                "title": "",
                "quality": "",
                "size": "",
                "type": link_type,
                "season": None,
                "episode": None,
                "links": {}
            }

            # Title from file-title div
            t = re.search(r'class="file-title"[^>]*>([^<]+)', part)
            if t:
                item["title"] = t.group(1).strip()
            else:
                h = re.search(r'flex-1[^>]*>\s*([A-Za-z0-9][^\n<]+)', part)
                if h:
                    item["title"] = h.group(1).strip()

            # Size (orange)
            s = re.search(r'#ea580c[^>]*>([^<]+)', part)
            if s:
                item["size"] = s.group(1).strip()

            # Quality (blue)
            q = re.search(r'#1e40af[^>]*>([^<]+)', part)
            if q:
                item["quality"] = q.group(1).strip()

            # Season
            sn = re.search(r'episode-number[^>]*>([^<]+)', part)
            if sn:
                item["season"] = sn.group(1).strip()

            # ALL gadgetsweb links
            links = re.findall(r'href="(https://gadgetsweb\.xyz/\?id=[^"]+)"', part)
            for i, lnk in enumerate(links):
                item["links"][f"link_{i+1}"] = lnk

            if item["links"]:
                items.append(item)

        return items

# =====================
# FastAPI APP
# =====================

app = FastAPI(
    title="HDHub Bypass API",
    description="API to scrape and bypass HDHub download links",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

bypasser = HDHubBypass()
scraper = HDHubScraper()
executor = ThreadPoolExecutor(max_workers=5)

@app.get("/")
async def root():
    return {
        "message": "HDHub Bypass API",
        "endpoints": {
            "/scrape": "POST - Extract download links from page",
            "/bypass": "POST - Resolve gadgetsweb URL to direct link",
            "/bypass_all": "POST - Scrape and bypass all links"
        }
    }

@app.post("/scrape", response_model=ScrapeResponse)
async def scrape_page(req: ScrapeRequest):
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(executor, scraper.scrape_page, req.url)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/bypass", response_model=BypassResponse)
async def bypass_link(req: BypassRequest):
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(executor, bypasser.bypass_gadgetsweb, req.url)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/bypass_all")
async def bypass_all_links(req: ScrapeRequest):
    try:
        loop = asyncio.get_event_loop()
        scraped = await loop.run_in_executor(executor, scraper.scrape_page, req.url)

        results = {
            "title": scraped["title"],
            "type": scraped["type"],
            "batch": [],
            "singles": []
        }

        async def bypass_item(item, cat):
            bypassed = {"info": item, "direct_links": {}}
            for name, url in item.get("links", {}).items():
                try:
                    r = await loop.run_in_executor(executor, bypasser.bypass_gadgetsweb, url)
                    bypassed["direct_links"][name] = r.get("final_url")
                except:
                    bypassed["direct_links"][name] = None
            return cat, bypassed

        tasks = []
        for item in scraped.get("batch", []):
            tasks.append(bypass_item(item, "batch"))
        for item in scraped.get("singles", []):
            tasks.append(bypass_item(item, "singles"))

        if tasks:
            done = await asyncio.gather(*tasks)
            for cat, bypassed in done:
                results[cat].append(bypassed)

        return results

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
