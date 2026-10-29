# Deployment report (Assignment 2)

`Tatkal_Deployment_Report.pdf` is the submitted report: 5 pages plus appendices (commands, evidence index,
the other problems we hit, screenshots). The `.docx` has the same content in Word format. Both are built
from `report.md`; the figures come from `../diagrams/` and the screenshots from `../../evidence/deployment/`.

To rebuild after editing `report.md`:

```bash
pip install pypandoc_binary pymupdf               # pandoc comes with pypandoc_binary
python docs/deployment-report/build_report.py     # needs Chrome, Chromium or Edge for the PDF
```

It works like the Assignment 1 report and uses its stylesheet, Lua filter and Word template from
`../report/`. The script also prints how many pages come before the appendices, since the brief allows 5.
