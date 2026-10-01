from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd


REQUIRED_SOURCES = {
    "current_movements": {"AiB", "PSP"},
    "asset_master": {"AiB"},
    "projects": {"PSP"},
    "cji3": {"AiB", "PSP", "GLAccount"},
    "vendors": {"Vendor", "CountryCode"},
    "cost_centers": {"CostCenter"},
}
OPTIONAL_SOURCES = {"annual_movements", "previous_quarter"}
BUSINESS_KEY = ["AiB", "PSP"]

DEFAULT_RULES: dict[str, Any] = {
    "excluded_gl_accounts": [],
    "domestic_country_code": "HU",
    "domestic_project_types": ["ME", "WE", "WW"],
    "startup_project_types": [],
    "intangible_asset_classes": ["S1"],
    "building_asset_classes": [],
    "building_cost_centers": ["658173"],
    "machine_project_types": ["TB"],
}


@dataclass
class ProcessingResult:
    master: pd.DataFrame
    exceptions: pd.DataFrame
    audit: pd.DataFrame
    summary: dict[str, Any]
    errors: list[str] = field(default_factory=list)


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    normalized.columns = [str(column).strip() for column in normalized.columns]
    for column in normalized.columns:
        if normalized[column].dtype == "object" or pd.api.types.is_string_dtype(normalized[column]):
            normalized[column] = normalized[column].map(
                lambda value: value.strip() if isinstance(value, str) else value
            )
    return normalized


def validate_sources(sources: dict[str, pd.DataFrame]) -> list[str]:
    errors: list[str] = []
    for role, required_columns in REQUIRED_SOURCES.items():
        if role not in sources or sources[role] is None:
            errors.append(f"Missing required source: {role.replace('_', ' ')}")
            continue
        missing = required_columns - set(sources[role].columns)
        if missing:
            errors.append(f"{role.replace('_', ' ')} is missing columns: {', '.join(sorted(missing))}")
    return errors


def _safe_merge(left: pd.DataFrame, right: pd.DataFrame, key: str | list[str]) -> pd.DataFrame:
    if right.empty:
        return left
    right = right.drop_duplicates(subset=key, keep="first")
    return left.merge(right, on=key, how="left", suffixes=("", "_source"), validate="many_to_one")


def _join_sources(sources: dict[str, pd.DataFrame], rules: dict[str, Any]) -> tuple[pd.DataFrame, int]:
    master = normalize_frame(sources["current_movements"])
    for key in BUSINESS_KEY:
        master[key] = master[key].astype("string").str.strip()

    asset = normalize_frame(sources["asset_master"])
    project = normalize_frame(sources["projects"])
    centers = normalize_frame(sources["cost_centers"])
    vendors = normalize_frame(sources["vendors"])
    cji3 = normalize_frame(sources["cji3"])

    master = _safe_merge(master, asset, "AiB")
    master = _safe_merge(master, project, "PSP")
    if "CostCenter" in master.columns and "CostCenter" in centers.columns:
        master = _safe_merge(master, centers, "CostCenter")

    cji3["GLAccount"] = cji3["GLAccount"].astype("string").str.strip()
    excluded = {str(value).strip() for value in rules.get("excluded_gl_accounts", [])}
    before = len(cji3)
    if excluded:
        cji3 = cji3.loc[~cji3["GLAccount"].isin(excluded)].copy()
    removed_rows = before - len(cji3)

    vendors["Vendor"] = vendors["Vendor"].astype("string").str.strip()
    if "Vendor" in cji3.columns:
        cji3["Vendor"] = cji3["Vendor"].astype("string").str.strip()
        cji3 = cji3.merge(
            vendors[[column for column in ["Vendor", "CountryCode", "VendorName"] if column in vendors]],
            on="Vendor",
            how="left",
            suffixes=("", "_vendor"),
        )

    grouped_records: list[dict[str, Any]] = []
    for key, group in cji3.groupby(BUSINESS_KEY, dropna=False, sort=False):
        countries = sorted(
            {str(value).strip().upper() for value in group.get("CountryCode", pd.Series(dtype=object)).dropna() if str(value).strip()}
        )
        grouped_records.append(
            {
                "AiB": key[0],
                "PSP": key[1],
                "SupplierCountries": ", ".join(countries),
                "CountryCount": len(countries),
                "SupplierCount": int(group["Vendor"].nunique()) if "Vendor" in group else 0,
            }
        )
    country_rollup = pd.DataFrame(grouped_records, columns=BUSINESS_KEY + ["SupplierCountries", "CountryCount", "SupplierCount"])
    master = _safe_merge(master, country_rollup, BUSINESS_KEY)

    annual = sources.get("annual_movements")
    if annual is not None and not annual.empty and set(BUSINESS_KEY).issubset(annual.columns):
        annual = normalize_frame(annual)
        for key in BUSINESS_KEY:
            annual[key] = annual[key].astype("string").str.strip()
        if "Amount" in annual.columns:
            annual["Amount"] = pd.to_numeric(annual["Amount"].astype("string").str.replace(",", "", regex=False), errors="coerce")
            annual_totals = annual.groupby(BUSINESS_KEY, dropna=False, as_index=False)["Amount"].sum(min_count=1)
            annual_totals = annual_totals.rename(columns={"Amount": "FullYearAmount"})
            master = _safe_merge(master, annual_totals, BUSINESS_KEY)

    if "CountryCode" not in master:
        master["CountryCode"] = pd.NA
    country_fallback = master.get("SupplierCountries", pd.Series(index=master.index, dtype="object")).where(
        master.get("CountryCount", pd.Series(index=master.index, dtype="float64")).eq(1)
    )
    master["CountryCode"] = master["CountryCode"].where(master["CountryCode"].notna(), country_fallback)
    return master, removed_rows


def _classify(row: pd.Series, rules: dict[str, Any]) -> tuple[str, str, float, str]:
    asset_class = str(row.get("AssetClass", "") or "").strip().upper()
    project_type = str(row.get("ProjectType", "") or "").strip().upper()
    cost_center = str(row.get("CostCenter", "") or "").strip()
    building_classes = {str(value).upper() for value in rules.get("building_asset_classes", [])}
    intangible_classes = {str(value).upper() for value in rules.get("intangible_asset_classes", [])}
    building_centers = {str(value) for value in rules.get("building_cost_centers", [])}
    machine_types = {str(value).upper() for value in rules.get("machine_project_types", [])}

    if asset_class in building_classes or cost_center in building_centers:
        return "Building", "CLASS-BUILDING", 0.98, "Building asset class or configured building cost center"
    if asset_class in intangible_classes:
        return "Intangible Asset", "CLASS-INTANGIBLE", 0.96, "Configured intangible asset class"
    if project_type in machine_types:
        return "Machine", "CLASS-MACHINE-PROJECT", 0.92, "Configured machine project type"
    return "Machine", "CLASS-DEFAULT", 0.72, "Default classification; confirm when asset details are unclear"


def process_sources(
    sources: dict[str, pd.DataFrame],
    rules: dict[str, Any] | None = None,
    quarter: str = "",
) -> ProcessingResult:
    configured = {**DEFAULT_RULES, **(rules or {})}
    normalized_sources = {role: normalize_frame(frame) for role, frame in sources.items() if frame is not None}
    errors = validate_sources(normalized_sources)
    if errors:
        empty = pd.DataFrame()
        return ProcessingResult(empty, empty, empty, {}, errors)

    master, excluded_rows = _join_sources(normalized_sources, configured)
    for column in ["ProjectType", "AssetClass", "CostCenter", "CountryCode"]:
        if column not in master:
            master[column] = ""

    origins: list[str] = []
    classifications: list[str] = []
    applied_rules: list[str] = []
    confidence: list[float] = []
    reasons: list[str] = []
    startup_flags: list[str] = []
    used_flags: list[str] = []
    exception_reasons: list[str] = []
    startup_types = {str(value).upper() for value in configured.get("startup_project_types", [])}
    domestic_types = {str(value).upper() for value in configured.get("domestic_project_types", [])}
    domestic_country = str(configured.get("domestic_country_code", "HU")).upper()

    previous = normalized_sources.get("previous_quarter", pd.DataFrame())
    previous_lookup: dict[tuple[str, str], tuple[str, str]] = {}
    if not previous.empty and set(BUSINESS_KEY).issubset(previous.columns):
        previous = previous.drop_duplicates(BUSINESS_KEY, keep="last")
        for _, previous_row in previous.iterrows():
            previous_lookup[(str(previous_row["AiB"]), str(previous_row["PSP"]))] = (
                str(previous_row.get("Origin", "")),
                str(previous_row.get("Classification", "")),
            )

    for _, row in master.iterrows():
        project_type = str(row.get("ProjectType", "") or "").strip().upper()
        country = str(row.get("CountryCode", "") or "").strip().upper()
        country_count = row.get("CountryCount", 0)
        row_exceptions: list[str] = []

        if project_type in domestic_types or country == domestic_country:
            origin, origin_rule = "Domestic", "ORG-DOMESTIC"
        elif country and country.lower() != "nan":
            origin, origin_rule = "Foreign", "ORG-FOREIGN"
        else:
            origin, origin_rule = "Review", "ORG-MISSING"
            row_exceptions.append("Supplier country is missing")
        if pd.notna(country_count) and country_count > 1:
            origin = "Review"
            row_exceptions.append("Multiple supplier countries are linked to this item")

        classification, class_rule, certainty, class_reason = _classify(row, configured)
        if class_rule == "CLASS-DEFAULT":
            row_exceptions.append("Default asset classification needs confirmation")

        key = (str(row["AiB"]), str(row["PSP"]))
        old = previous_lookup.get(key)
        if old and ((old[0] and old[0] != origin) or (old[1] and old[1] != classification)):
            row_exceptions.append("Decision differs from previous quarter")

        origins.append(origin)
        classifications.append(classification)
        applied_rules.append(f"{origin_rule}; {class_rule}")
        confidence.append(certainty if origin != "Review" else min(certainty, 0.5))
        reasons.append(class_reason)
        startup_flags.append("Yes" if project_type in startup_types else "No")
        used_flags.append("Yes" if project_type == "MG" and origin == "Domestic" else "No")
        exception_reasons.append("; ".join(dict.fromkeys(row_exceptions)))

    master["Origin"] = origins
    master["Classification"] = classifications
    master["StartUp"] = startup_flags
    master["Used"] = used_flags
    master["RuleApplied"] = applied_rules
    master["RuleExplanation"] = reasons
    master["Confidence"] = confidence
    master["ExceptionReason"] = exception_reasons
    master["PreviousQuarterDecision"] = ""
    for index, row in master.iterrows():
        old = previous_lookup.get((str(row["AiB"]), str(row["PSP"])))
        if old:
            master.at[index, "PreviousQuarterDecision"] = f"{old[0]} / {old[1]}"

    exception_mask = master["ExceptionReason"].ne("")
    exceptions = master.loc[exception_mask].copy().reset_index(drop=True)
    exceptions["ReviewerOrigin"] = exceptions["Origin"].replace("Review", "")
    exceptions["ReviewerClassification"] = exceptions["Classification"]
    exceptions["ReviewerNotes"] = ""
    exceptions["ReviewedBy"] = ""

    now = datetime.now(timezone.utc).isoformat()
    audit = pd.DataFrame(
        [
            {"Event": "run_started", "TimestampUTC": now, "Detail": f"Quarter: {quarter or 'unspecified'}"},
            {"Event": "sources_loaded", "TimestampUTC": now, "Detail": ", ".join(sorted(normalized_sources))},
            {"Event": "cji3_rows_excluded", "TimestampUTC": now, "Detail": str(excluded_rows)},
            {"Event": "records_processed", "TimestampUTC": now, "Detail": str(len(master))},
        ]
    )
    missing_country = master["CountryCode"].isna() | master["CountryCode"].astype("string").str.strip().eq("")
    summary = {
        "quarter": quarter or "Unspecified",
        "processed": len(master),
        "auto_classified": int((~exception_mask).sum()),
        "exceptions": int(exception_mask.sum()),
        "missing_country": int(missing_country.sum()),
        "excluded_cji3_rows": excluded_rows,
        "changed_since_previous": int(master["ExceptionReason"].str.contains("previous quarter", case=False).sum()),
        "source_count": len(normalized_sources),
    }
    return ProcessingResult(master.reset_index(drop=True), exceptions, audit, summary)


def apply_reviews(master: pd.DataFrame, reviewed: pd.DataFrame, reviewer: str = "") -> tuple[pd.DataFrame, pd.DataFrame]:
    result = master.copy()
    audit_rows: list[dict[str, Any]] = []
    if reviewed.empty:
        return result, pd.DataFrame(audit_rows)
    for _, decision in reviewed.iterrows():
        mask = pd.Series(True, index=result.index)
        for key in BUSINESS_KEY:
            mask &= result[key].astype(str).eq(str(decision[key]))
        matches = result.index[mask]
        if not len(matches):
            continue
        index = matches[0]
        for output_column, input_column in [("Origin", "ReviewerOrigin"), ("Classification", "ReviewerClassification")]:
            value = decision.get(input_column)
            if pd.notna(value) and str(value).strip():
                result.at[index, output_column] = str(value).strip()
        if result.at[index, "Origin"] != "Review" and result.at[index, "Classification"] in {
            "Building", "Machine", "Intangible Asset"
        }:
            result.at[index, "ExceptionReason"] = ""
        else:
            continue
        audit_rows.append(
            {
                "Event": "review_decision_applied",
                "TimestampUTC": datetime.now(timezone.utc).isoformat(),
                "AiB": str(decision["AiB"]),
                "PSP": str(decision["PSP"]),
                "Reviewer": reviewer or str(decision.get("ReviewedBy", "")),
                "Notes": str(decision.get("ReviewerNotes", "")),
                "Decision": f"{result.at[index, 'Origin']} / {result.at[index, 'Classification']}",
            }
        )
    return result, pd.DataFrame(audit_rows)