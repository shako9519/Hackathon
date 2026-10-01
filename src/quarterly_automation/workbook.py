from __future__ import annotations

from io import BytesIO
from typing import Any

import pandas as pd


def workbook_bytes(master: pd.DataFrame, exceptions: pd.DataFrame, audit: pd.DataFrame, summary: dict[str, Any]) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        master.to_excel(writer, sheet_name="Master_List", index=False)
        exceptions.to_excel(writer, sheet_name="Exception_Review", index=False)
        pd.DataFrame([summary]).to_excel(writer, sheet_name="Run_Summary", index=False)
        audit.to_excel(writer, sheet_name="Audit_Log", index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for column_cells in worksheet.columns:
                width = min(max(max(len(str(cell.value or "")) for cell in column_cells) + 2, 12), 42)
                worksheet.column_dimensions[column_cells[0].column_letter].width = width
    return output.getvalue()