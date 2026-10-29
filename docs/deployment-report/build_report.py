#!/usr/bin/env python3
"""Builds the Assignment 2 deployment report from report.md, the same way as the
Assignment 1 report (styles, filter and Word template are shared with ../report):

    Tatkal_Deployment_Report.pdf   - HTML (pandoc) printed by headless Chrome/Edge, footer added with PyMuPDF
    Tatkal_Deployment_Report.docx  - Word version, same content

Needs:  pip install pypandoc_binary pymupdf
        Google Chrome, Chromium or Microsoft Edge installed (or set CHROME=/path/to/browser)

    python docs/deployment-report/build_report.py
"""
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pymupdf
import pypandoc

HERE = Path(__file__).resolve().parent
SHARED = Path("..") / "report"
NAME = "Tatkal_Deployment_Report"
FOOTER = "Tatkal Booking System - Scalable Services (SEZG583) Assignment 2"
FIRST_APPENDIX = "Appendix A"


def find_browser() -> str:
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    candidates = {
        "Darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   "/Applications/Chromium.app/Contents/MacOS/Chromium",
                   "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
        "Windows": [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"],
    }.get(platform.system(), [])
    candidates += [shutil.which(n) or "" for n in ("google-chrome", "chromium", "chromium-browser", "msedge")]
    for path in candidates:
        if path and Path(path).exists():
            return path
    sys.exit("No Chrome/Chromium/Edge found - set CHROME=/path/to/browser")


def pandoc(*args: str) -> None:
    subprocess.run([pypandoc.get_pandoc_path(), *args], cwd=HERE, check=True)


def build_html(build: Path) -> Path:
    html = build / "report.html"
    pandoc("report.md", "-t", "html5", "-o", str(html), "--standalone", "--embed-resources",
           f"--css={SHARED / 'report.css'}", f"--lua-filter={SHARED / 'filters.lua'}", "--resource-path=.",
           "--metadata=pagetitle:Tatkal Booking System - Deployment Report")
    return html


def build_pdf(html: Path, build: Path) -> Path:
    raw = build / "raw.pdf"
    subprocess.run([find_browser(), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--user-data-dir={build / 'chrome-profile'}", f"--print-to-pdf={raw}", html.as_uri()],
                   check=True, capture_output=True, timeout=180)
    out = HERE / f"{NAME}.pdf"
    doc = pymupdf.open(raw)
    total = doc.page_count
    for number, page in enumerate(doc, start=1):
        width = page.rect.width
        y = page.rect.height - 22
        page.insert_text((42, y), FOOTER, fontsize=7.5, color=(0.45, 0.45, 0.45))
        label = f"page {number} of {total}"
        page.insert_text((width - 42 - pymupdf.get_text_length(label, fontsize=7.5), y), label,
                         fontsize=7.5, color=(0.45, 0.45, 0.45))
    doc.set_metadata({"title": "Tatkal Booking System - Deployment Report",
                      "subject": "Scalable Services (SEZG583) Assignment 2"})
    doc.save(out)
    return out


def build_docx() -> Path:
    out = HERE / f"{NAME}.docx"
    pandoc("report.md", "-t", "docx", "-o", str(out), f"--lua-filter={SHARED / 'filters.lua'}",
           "--resource-path=.", f"--reference-doc={SHARED / 'reference.docx'}")
    return out


def main_part_pages(pdf: Path) -> int:
    """Pages before the first appendix (the brief allows 5)."""
    with pymupdf.open(pdf) as doc:
        for page in doc:
            if page.search_for(FIRST_APPENDIX):
                return page.number
        return doc.page_count


def main() -> None:
    build = HERE / "build"
    build.mkdir(exist_ok=True)
    pdf = build_pdf(build_html(build), build)
    pages = pymupdf.open(pdf).page_count
    print(f"{pdf.name}: {pages} pages, {main_part_pages(pdf)} before the appendices")
    print(f"{build_docx().name}: written")
    shutil.rmtree(build, ignore_errors=True)


if __name__ == "__main__":
    main()
