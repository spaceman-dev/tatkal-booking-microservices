# Report

`Tatkal_Booking_System_Design_Report.pdf` is the submitted report (13 pages plus appendices). The `.docx`
has the same content in Word format. Both are built from `report.md`; the figures come from
`../diagrams/`.

To rebuild after editing `report.md`:

```bash
pip install pypandoc_binary pymupdf      # pandoc comes with pypandoc_binary
python docs/report/build_report.py       # needs Chrome, Chromium or Edge for the PDF
```

pandoc turns the Markdown into HTML (styled by `report.css`), headless Chrome prints it to PDF and PyMuPDF
adds the page footer. The Word file uses `reference.docx` for its styles and `filters.lua` for the page
breaks and images.
