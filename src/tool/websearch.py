"""WebSearch tool: web search via DuckDuckGo (no API key required)."""

import json
import urllib.parse

import httpx

from src.tool.base import Tool, ToolContext, ToolResult


class WebSearchTool(Tool):
    name = "websearch"
    description = "Search the web for information using DuckDuckGo."
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results (1-10, default 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        query = params["query"]
        max_results = min(int(params.get("max_results", 5)), 10)

        try:
            results = await self._search(query, max_results)
            if not results:
                return ToolResult(output=f"No results found for: {query}")
            return ToolResult(output=json.dumps(results, ensure_ascii=False, indent=2))
        except Exception as e:
            return ToolResult(output=f"Search failed: {e}")

    async def _search(self, query: str, max_results: int) -> list[dict]:
        encoded = urllib.parse.quote(query)
        url = f"https://lite.duckduckgo.com/lite/?q={encoded}"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                },
                follow_redirects=True,
            )
            resp.raise_for_status()

        return self._parse_results(resp.text, max_results)

    def _parse_results(self, html: str, max_results: int) -> list[dict]:
        results: list[dict] = []

        # DuckDuckGo Lite 的简单 HTML 解析
        # 提取 <a> 标签和后续的 <span class="link-text">
        import re

        rows = re.findall(
            r'<a\s+rel="nofollow"\s+href="([^"]+)"\s+class="result-link">([^<]+)</a>.*?'
            r'<span class="link-text">([^<]*)</span>.*?<td class="result-snippet">(.*?)</td>',
            html,
            re.DOTALL,
        )

        for url, title, _display_url, snippet in rows:
            snippet = re.sub(r"<[^>]+>", "", snippet).strip()
            results.append(
                {
                    "title": title.strip(),
                    "url": urllib.parse.unquote(url),
                    "snippet": snippet,
                }
            )
            if len(results) >= max_results:
                break

        # 备选：如果没有匹配到带 snippet 的格式，尝试简单提取链接和标题
        if not results:
            links = re.findall(
                r'<a\s+rel="nofollow"\s+href="([^"]+)"\s+class="result-link">([^<]+)</a>',
                html,
            )
            for url, title in links[:max_results]:
                results.append(
                    {
                        "title": title.strip(),
                        "url": urllib.parse.unquote(url),
                        "snippet": "",
                    }
                )

        return results
