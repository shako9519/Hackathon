# Quarterly Investment Statistics Automation

A file-driven MVP for consolidating quarterly SAP exports, applying explainable deterministic rules, reviewing exceptions, and exporting an auditable workbook. SAP extraction is deliberately outside this prototype so the same processing core can later be called by an approved SAP interface or RPA job.

## Run locally

Requires Python 3.10 or later.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

Run core tests with:

```powershell
python -m pytest
```

The app opens with demo data enabled where sample files exist. Replace any source with an XLSX or CSV export. Required sources are current-quarter movements, ANLA asset master, CN43N projects, CJI3 line items, LFA1 vendors, and the cost-center plan. Full-year movements and the prior-quarter master are optional.

## Input schema

Column names are matched exactly after trimming whitespace. Required columns:

| Source role | Required columns |
| --- | --- |
| `current_movements` | `AiB`, `PSP` |
| `asset_master` | `AiB` |
| `projects` | `PSP` |
| `cji3` | `AiB`, `PSP`, `GLAccount` |
| `vendors` | `Vendor`, `CountryCode` |
| `cost_centers` | `CostCenter` |
| `previous_quarter` (optional) | `AiB`, `PSP`, `Origin`, `Classification` for comparison |

Useful enrichment columns include `PostingDate`, `Amount`, `Currency`, `AssetDescription`, `AssetClass`, `CostCenter`, `ProjectType`, `Vendor`, and `VendorName`. Files may have additional columns. Excel imports use the first worksheet.

## Rules and review

Rules live in `config/rules.json` and can be adjusted from the run controls. Defaults classify configured building cost centers and asset classes as Building, configured class `S1` as Intangible Asset, configured project types as Machine, and fall back to Machine with an exception. Domestic origin is assigned from configured domestic project types or country code; multiple supplier countries and missing countries require review. `Used` is set for project type `MG` with Domestic origin. These defaults are prototype examples and must be confirmed by the process owner before production use.

Every master row includes the applied rule IDs, explanation, confidence, exception reason, and prior-quarter decision. Review edits update the master and add a reviewer event to the audit sheet. Downloaded workbooks contain `Master_List`, `Exception_Review`, `Run_Summary`, and `Audit_Log` sheets.

## Production boundary

This prototype does not log into SAP, schedule unattended runs, retain historical reviewer decisions between sessions, or authenticate reviewers. Input files remain in the browser session and are processed by the local Streamlit process. Before production, confirm business keys, GL exclusions, rule priority, start-up rules, country aggregation, and amendment/audit retention with the process owner. Add approved credential handling, access control, immutable raw-file storage, and monitored SAP extraction separately.