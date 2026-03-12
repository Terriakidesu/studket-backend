from html import escape
from pathlib import Path
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Account, Listing, ListingInventory, ListingMedia, ListingTag, Review, Tag, UserProfile
from app.db.session import get_db

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory="app/templates")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
API_REFERENCE_PATH = PROJECT_ROOT / "API_REFERENCE.md"


def _llm_docs_text() -> str:
    markdown_text = API_REFERENCE_PATH.read_text(encoding="utf-8").strip()
    return (
        "# Studket API Docs\n\n"
        "Machine-friendly API reference for LLMs and tooling.\n\n"
        "Human-readable HTML docs: /docs\n"
        "Swagger UI: /swagger\n"
        "Source markdown: /docs/llm\n\n"
        f"{markdown_text}\n"
    )


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


@router.get("/docs/llm", response_class=PlainTextResponse)
def api_docs_llm() -> str:
    return _llm_docs_text()


@router.get("/llms.txt", response_class=PlainTextResponse)
def llms_txt() -> str:
    return (
        "Studket API Documentation\n"
        "HTML docs: /docs\n"
        "LLM-friendly markdown docs: /docs/llm\n"
        "Swagger UI: /swagger\n"
    )


@router.get("/share/{share_token}", response_class=HTMLResponse)
def share_listing_page(
    share_token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    def absolute_url(path_value: str | None) -> str | None:
        if not path_value:
            return None
        path_text = str(path_value).strip()
        if path_text.startswith(("http://", "https://")):
            return path_text
        return str(request.base_url).rstrip("/") + "/" + path_text.lstrip("/")

    listing = (
        db.query(Listing)
        .filter(Listing.share_token == share_token)
        .first()
    )
    if listing is None:
        return templates.TemplateResponse(
            "share_listing.html",
            {
                "request": request,
                "title": "Shared Listing",
                "listing": None,
            },
            status_code=404,
        )

    media = (
        db.query(ListingMedia)
        .filter(ListingMedia.listing_id == listing.listing_id)
        .order_by(ListingMedia.sort_order.asc(), ListingMedia.media_id.asc())
        .all()
    )
    tags = (
        db.query(Tag.tag_name)
        .join(ListingTag, ListingTag.tag_id == Tag.tag_id)
        .filter(ListingTag.listing_id == listing.listing_id)
        .order_by(Tag.tag_name.asc())
        .all()
    )
    inventory = (
        db.query(ListingInventory)
        .filter(ListingInventory.listing_id == listing.listing_id)
        .first()
    )
    seller_row = (
        db.query(
            Account.username.label("seller_username"),
            UserProfile.first_name.label("seller_first_name"),
            UserProfile.last_name.label("seller_last_name"),
            UserProfile.campus.label("seller_campus"),
            UserProfile.profile_photo.label("seller_profile_photo"),
            UserProfile.is_verified.label("seller_is_verified"),
        )
        .outerjoin(UserProfile, UserProfile.user_id == Account.account_id)
        .filter(Account.account_id == listing.seller_id)
        .first()
    )
    rating_row = (
        db.query(
            func.avg(Review.rating).label("average_rating"),
            func.count(Review.review_id).label("review_count"),
        )
        .filter(Review.reviewee_id == listing.seller_id)
        .first()
    )
    seller_username = seller_row.seller_username if seller_row else None
    seller_full_name = None
    if seller_row:
        seller_full_name = " ".join(
            value for value in [seller_row.seller_first_name, seller_row.seller_last_name] if value
        ) or None
    seller_display_name = seller_full_name or seller_username or "Unknown seller"
    preview_description = (
        (listing.description or "").strip()
        or f"Shared by {seller_display_name} on Studket."
    )
    preview_description = " ".join(preview_description.split())
    if len(preview_description) > 190:
        preview_description = preview_description[:187].rstrip() + "..."
    primary_media_url = absolute_url(media[0].file_path) if media else None
    share_url = str(request.url)
    return templates.TemplateResponse(
        "share_listing.html",
        {
            "request": request,
            "title": listing.title,
            "listing": listing,
            "listing_media": media,
            "listing_tags": [row.tag_name for row in tags],
            "listing_inventory": inventory,
            "seller_username": seller_username,
            "seller_full_name": seller_full_name,
            "seller_campus": seller_row.seller_campus if seller_row else None,
            "seller_profile_photo": seller_row.seller_profile_photo if seller_row else None,
            "seller_is_verified": bool(seller_row.seller_is_verified) if seller_row else False,
            "seller_average_rating": float(rating_row.average_rating or 0) if rating_row else 0,
            "seller_review_count": int(rating_row.review_count or 0) if rating_row else 0,
            "share_token": share_token,
            "share_url": share_url,
            "social_title": listing.title,
            "social_description": preview_description,
            "social_image_url": primary_media_url,
            "social_site_name": "Studket",
        },
    )
