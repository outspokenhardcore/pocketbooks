"""The App Store listing that ships in ios/fastlane/metadata.

App Store Connect rejects text that is too long only at upload time, which is
the worst moment to find out. These limits come from Apple's field
specifications; the checks also keep the listing honest about what the app is.
"""
import re
from pathlib import Path

import pytest

METADATA = Path(__file__).resolve().parents[2] / "ios" / "fastlane" / "metadata"
LISTING = METADATA / "en-US"

LIMITS = {
    "name": 30,
    "subtitle": 30,
    "promotional_text": 170,
    "keywords": 100,
    "description": 4000,
    "release_notes": 4000,
}


def read(name: str) -> str:
    return (LISTING / f"{name}.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("field,limit", LIMITS.items())
def test_field_fits_apples_limit(field, limit):
    text = read(field)
    assert text.strip(), f"{field} is empty"
    assert len(text) <= limit, f"{field} is {len(text)} characters, limit {limit}"


@pytest.mark.parametrize("field", LIMITS)
def test_no_stray_whitespace_around_single_line_fields(field):
    if field in {"description", "release_notes"}:
        return
    assert read(field) == read(field).strip()
    assert "\n" not in read(field)


def test_keywords_are_comma_separated_without_spaces_and_unique():
    keywords = read("keywords").split(",")
    assert all(k == k.strip() and k for k in keywords)
    assert len(set(k.lower() for k in keywords)) == len(keywords)
    # The app name and category are indexed automatically; repeating wastes space.
    assert "pocketbooks" not in [k.lower() for k in keywords]


def test_urls_are_https_and_point_at_pages_that_exist_in_the_repo():
    site = Path(__file__).resolve().parents[2] / "web" / "site"
    for field, page in (("support_url", "support.html"),
                        ("privacy_url", "privacy.html"),
                        ("marketing_url", "index.html")):
        url = read(field)
        assert url.startswith("https://www.mauli.app/pocketbooks/"), field
        assert (site / page).exists(), f"{page} is missing from web/site"


def test_category_is_books():
    assert (METADATA / "primary_category.txt").read_text() == "BOOKS"


def test_review_notes_explain_the_demo_path_and_the_ats_exception():
    notes = (METADATA / "review_information" / "notes.txt").read_text(encoding="utf-8")
    assert "Try the demo library" in notes
    assert "App Transport Security" in notes
    assert "no login" in notes.lower()
    # The reviewer must be able to reach the catalogue the notes point at.
    assert "https://www.mauli.app/pocketbooks/catalogue/" in notes


def test_listing_does_not_promise_things_the_app_cannot_do():
    text = " ".join(read(f) for f in ("description", "promotional_text")).lower()
    for claim in ("android", "windows", "cloud sync", "free trial", "subscription",
                  "voice clon", "ocr", "no internet required"):
        # "no cloud" is a feature; only flag affirmative uses.
        if claim == "cloud sync":
            assert "cloud sync" not in text
        elif claim == "voice clon":
            assert "voice cloning" not in text or "no voice cloning" in text
        else:
            assert claim not in text, claim


def test_description_names_every_demo_book():
    from app import samples
    description = read("description")
    for book in samples.load_catalog():
        assert book.title in description, book.title


def test_nothing_personal_is_committed_with_the_listing():
    for path in METADATA.rglob("*.txt"):
        body = path.read_text(encoding="utf-8")
        assert not re.search(r"\+\d[\d ]{7,}", body), f"phone number in {path.name}"
        assert "@" not in body or "github" in body, f"email address in {path.name}"
    assert not (METADATA / "copyright.txt").exists()
    assert not (METADATA / "review_information" / "phone_number.txt").exists()
