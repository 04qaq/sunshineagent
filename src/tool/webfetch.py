"""WebFetch tool: fetch URL content."""

import httpx

from src.tool.base import Tool, ToolContext, ToolResult


class WebFetchTool(Tool):
    name = "webfetch"
    description = "Fetches content from a specified URL."
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch content from",
            },
            "format": {
                "type": "string",
                "enum": ["text", "markdown", "html"],
                "description": "The format to return the content in",
            },
            "timeout": {
                "type": "number",
                "description": "Optional timeout in seconds (max 120)",
            },
        },
        "required": ["url"],
    }

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        url = params["url"]
        timeout = min(params.get("timeout", 30), 120)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()

                content_type = response.headers.get("content-type", "")
                if "text/html" in content_type:
                    output = response.text[:50000]
                    if len(response.text) > 50000:
                        output += "\n... [content truncated]"
                    return ToolResult(output=output)

                return ToolResult(output=response.text[:50000])

        except httpx.HTTPStatusError as e:
            return ToolResult(output=f"HTTP error: {e.response.status_code}")
        except httpx.TimeoutException:
            return ToolResult(output="Request timed out")
        except Exception as e:
            return ToolResult(output=f"Fetch error: {e}")
