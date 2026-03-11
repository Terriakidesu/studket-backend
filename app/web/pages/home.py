from html import escape
from pathlib import Path
import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory="app/templates")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
API_REFERENCE_PATH = PROJECT_ROOT / "API_REFERENCE.md"


def _render_inline_markdown(text: str) -> str:
    escaped = escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', escaped)
    return escaped


def _render_markdown_html(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    html_parts: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    code_lines: list[str] = []
    in_code_block = False
    code_language = ""

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            html_parts.append(f"<p>{_render_inline_markdown(' '.join(paragraph).strip())}</p>")
            paragraph = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            html_parts.append("<ul>" + "".join(list_items) + "</ul>")
            list_items = []

    def flush_code() -> None:
        nonlocal code_lines, code_language
        if code_lines:
            language_class = f' class="language-{escape(code_language)}"' if code_language else ""
            html_parts.append(
                f"<pre><code{language_class}>{escape(chr(10).join(code_lines))}</code></pre>"
            )
            code_lines = []
            code_language = ""

    for raw_line in lines:
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_paragraph()
            flush_list()
            if in_code_block:
                flush_code()
                in_code_block = False
            else:
                in_code_block = True
                code_language = stripped[3:].strip()
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        if not stripped:
            flush_paragraph()
            flush_list()
            continue

        if stripped.startswith("#"):
            flush_paragraph()
            flush_list()
            level = min(len(stripped) - len(stripped.lstrip("#")), 6)
            content = stripped[level:].strip()
            html_parts.append(f"<h{level}>{_render_inline_markdown(content)}</h{level}>")
            continue

        if stripped.startswith("- "):
            flush_paragraph()
            list_items.append(f"<li>{_render_inline_markdown(stripped[2:].strip())}</li>")
            continue

        paragraph.append(stripped)

    flush_paragraph()
    flush_list()
    flush_code()
    return "\n".join(html_parts)


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        "home.html",
        {"request": request, "title": "Home"}
    )


@router.get("/docs", response_class=HTMLResponse)
def api_docs(request: Request):
    markdown_text = API_REFERENCE_PATH.read_text(encoding="utf-8")
    return templates.TemplateResponse(
        "api_docs.html",
        {
            "request": request,
            "title": "API Docs",
            "api_reference_html": _render_markdown_html(markdown_text),
        },
    )
