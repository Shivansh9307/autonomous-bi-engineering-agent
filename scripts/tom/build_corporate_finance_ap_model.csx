// Build CorporateFinanceAP semantic model deterministically via Tabular Editor 2 TOM.

Model.DiscourageImplicitMeasures = true;
Model.DefaultPowerBIDataSourceVersion = PowerBIDataSourceVersion.PowerBI_V3;
Model.SourceQueryCulture = "en-US";
Model.SetAnnotation("__PBI_TimeIntelligenceEnabled", "0");

foreach (var r in Model.Relationships.ToList()) r.Delete();
foreach (var role in Model.Roles.ToList()) role.Delete();
foreach (var t in Model.Tables.ToList()) t.Delete();
foreach (var ex in Model.Expressions.ToList()) ex.Delete();
foreach (var ds in Model.DataSources.ToList()) ds.Delete();

var dataFolder = Model.AddExpression("DataFolder");
dataFolder.Kind = ExpressionKind.M;
dataFolder.Expression = "\"C:\\\\Users\\\\Shivansh\\\\Projects\\\\Antigravity\\\\test_neon\\\\data\\\\finance_ap\\\\staged\" meta [IsParameterQuery=true, Type=\"Text\", IsParameterQueryRequired=true]";

Func<Table, string, DataType, bool, bool, DataColumn> addCol = (tbl, colName, dtype, hidden, mdx) =>
{
    var col = tbl.AddDataColumn(colName);
    col.SourceColumn = colName;
    col.DataType = dtype;
    col.IsHidden = hidden;
    col.IsAvailableInMDX = mdx;
    col.SummarizeBy = AggregateFunction.None;
    return col;
};

Action<Table, string, string> setM = (tbl, partName, mExpr) =>
{
    var oldParts = tbl.Partitions.ToList();
    var mp = tbl.AddMPartition(partName + "_M");
    mp.Expression = mExpr;
    mp.Mode = ModeType.Import;
    foreach (var p in oldParts) p.Delete();
    mp.Name = partName;
};

// 1. DimDate
var tDate = Model.AddTable("DimDate");
tDate.Description = "Calendar date dimension covering 2024-2026.";
tDate.DataCategory = "Time";
addCol(tDate, "DateKey", DataType.Int64, true, false);
var cDate = addCol(tDate, "Date", DataType.DateTime, false, true);
cDate.IsKey = true;
cDate.FormatString = "yyyy-mm-dd";
var cYear = addCol(tDate, "Year", DataType.Int64, false, true);
cYear.FormatString = "0";
var cQtr = addCol(tDate, "Quarter", DataType.String, false, true);
var cQtrNum = addCol(tDate, "QuarterNumber", DataType.Int64, true, true);
var cMonthNum = addCol(tDate, "MonthNumber", DataType.Int64, true, true);
var cMonthName = addCol(tDate, "MonthName", DataType.String, false, true);
var cYearMonth = addCol(tDate, "YearMonth", DataType.String, false, true);
var cYmSort = addCol(tDate, "YearMonthSort", DataType.Int64, true, true);
addCol(tDate, "DayOfWeek", DataType.String, false, true);
var cIsWeekend = addCol(tDate, "IsWeekend", DataType.Int64, false, true);
cIsWeekend.FormatString = "0";
var cMonthStart = addCol(tDate, "MonthStart", DataType.DateTime, false, true);
cMonthStart.FormatString = "mmm yyyy";

cQtr.SortByColumn = cQtrNum;
cMonthName.SortByColumn = cMonthNum;
cYearMonth.SortByColumn = cYmSort;

var hCal = tDate.AddHierarchy("Calendar Hierarchy");
hCal.AddLevel(cYear);
hCal.AddLevel(cQtr);
hCal.AddLevel(cMonthName);

setM(tDate, "DimDate", @"let
    Source = Csv.Document(File.Contents(DataFolder & ""\DimDate.csv""), [Delimiter="","", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    #""Promoted Headers"" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #""Changed Type"" = Table.TransformColumnTypes(#""Promoted Headers"", {
        {""DateKey"", Int64.Type},
        {""Date"", type date},
        {""Year"", Int64.Type},
        {""Quarter"", type text},
        {""QuarterNumber"", Int64.Type},
        {""MonthNumber"", Int64.Type},
        {""MonthName"", type text},
        {""YearMonth"", type text},
        {""YearMonthSort"", Int64.Type},
        {""DayOfWeek"", type text},
        {""IsWeekend"", Int64.Type},
        {""MonthStart"", type date}
    }, ""en-US"")
in
    #""Changed Type""");

// 2. DimVendor
var tVendor = Model.AddTable("DimVendor");
tVendor.Description = "Supplier master dimension including -1 Unknown Vendor.";
addCol(tVendor, "VendorKey", DataType.Int64, true, false);
addCol(tVendor, "VendorCode", DataType.String, false, true);
var cVendorName = addCol(tVendor, "VendorName", DataType.String, false, true);
var cVendorCat = addCol(tVendor, "VendorCategory", DataType.String, false, true);
addCol(tVendor, "PaymentTerms", DataType.String, false, true);
var cVendorCountry = addCol(tVendor, "Country", DataType.String, false, true);
cVendorCountry.DataCategory = "Country";
addCol(tVendor, "VendorTier", DataType.String, false, true);

var hVendor = tVendor.AddHierarchy("Vendor Hierarchy");
hVendor.AddLevel(cVendorCat);
hVendor.AddLevel(cVendorName);

setM(tVendor, "DimVendor", @"let
    Source = Csv.Document(File.Contents(DataFolder & ""\DimVendor.csv""), [Delimiter="","", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    #""Promoted Headers"" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #""Changed Type"" = Table.TransformColumnTypes(#""Promoted Headers"", {
        {""VendorKey"", Int64.Type},
        {""VendorCode"", type text},
        {""VendorName"", type text},
        {""VendorCategory"", type text},
        {""PaymentTerms"", type text},
        {""Country"", type text},
        {""VendorTier"", type text}
    }, ""en-US"")
in
    #""Changed Type""");

// 3. DimCostCenter
var tCC = Model.AddTable("DimCostCenter");
tCC.Description = "Organizational cost center and business unit dimension.";
addCol(tCC, "CostCenterKey", DataType.Int64, true, false);
addCol(tCC, "CostCenterCode", DataType.String, false, true);
var cCCName = addCol(tCC, "CostCenterName", DataType.String, false, true);
var cBU = addCol(tCC, "BusinessUnit", DataType.String, false, true);
var cDept = addCol(tCC, "Department", DataType.String, false, true);
addCol(tCC, "EntityCode", DataType.String, false, true);

var hOrg = tCC.AddHierarchy("Organization Hierarchy");
hOrg.AddLevel(cBU);
hOrg.AddLevel(cDept);
hOrg.AddLevel(cCCName);

setM(tCC, "DimCostCenter", @"let
    Source = Csv.Document(File.Contents(DataFolder & ""\DimCostCenter.csv""), [Delimiter="","", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    #""Promoted Headers"" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #""Changed Type"" = Table.TransformColumnTypes(#""Promoted Headers"", {
        {""CostCenterKey"", Int64.Type},
        {""CostCenterCode"", type text},
        {""CostCenterName"", type text},
        {""BusinessUnit"", type text},
        {""Department"", type text},
        {""EntityCode"", type text}
    }, ""en-US"")
in
    #""Changed Type""");

// 4. DimGLAccount
var tGL = Model.AddTable("DimGLAccount");
tGL.Description = "General ledger expense account hierarchy dimension.";
addCol(tGL, "GLAccountKey", DataType.Int64, true, false);
addCol(tGL, "GLAccountCode", DataType.String, false, true);
var cGLName = addCol(tGL, "GLAccountName", DataType.String, false, true);
var cExpGrp = addCol(tGL, "ExpenseGroup", DataType.String, false, true);
addCol(tGL, "AccountType", DataType.String, false, true);

var hGL = tGL.AddHierarchy("GL Hierarchy");
hGL.AddLevel(cExpGrp);
hGL.AddLevel(cGLName);

setM(tGL, "DimGLAccount", @"let
    Source = Csv.Document(File.Contents(DataFolder & ""\DimGLAccount.csv""), [Delimiter="","", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    #""Promoted Headers"" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #""Changed Type"" = Table.TransformColumnTypes(#""Promoted Headers"", {
        {""GLAccountKey"", Int64.Type},
        {""GLAccountCode"", type text},
        {""GLAccountName"", type text},
        {""ExpenseGroup"", type text},
        {""AccountType"", type text}
    }, ""en-US"")
in
    #""Changed Type""");

// 5. DimApprover
var tApp = Model.AddTable("DimApprover");
tApp.Description = "AP invoice approver security dimension.";
addCol(tApp, "ApproverKey", DataType.Int64, true, false);
addCol(tApp, "ApproverCode", DataType.String, false, true);
addCol(tApp, "ApproverName", DataType.String, false, true);
addCol(tApp, "RoleTitle", DataType.String, false, true);
addCol(tApp, "BusinessUnit", DataType.String, false, true);
addCol(tApp, "UserPrincipalName", DataType.String, false, true);

setM(tApp, "DimApprover", @"let
    Source = Csv.Document(File.Contents(DataFolder & ""\DimApprover.csv""), [Delimiter="","", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    #""Promoted Headers"" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #""Changed Type"" = Table.TransformColumnTypes(#""Promoted Headers"", {
        {""ApproverKey"", Int64.Type},
        {""ApproverCode"", type text},
        {""ApproverName"", type text},
        {""RoleTitle"", type text},
        {""BusinessUnit"", type text},
        {""UserPrincipalName"", type text}
    }, ""en-US"")
in
    #""Changed Type""");

// 6. FactAPInvoices
var tInv = Model.AddTable("FactAPInvoices");
tInv.Description = "Staged AP invoice and credit memo line items at InvoiceID + LineNumber grain.";
addCol(tInv, "InvoiceID", DataType.String, false, true);
addCol(tInv, "LineNumber", DataType.Int64, true, false);
addCol(tInv, "PostingDateKey", DataType.Int64, true, false);
var cInvPostDt = addCol(tInv, "PostingDate", DataType.DateTime, true, false);
cInvPostDt.FormatString = "yyyy-mm-dd";
var cInvDueDt = addCol(tInv, "DueDate", DataType.DateTime, true, false);
cInvDueDt.FormatString = "yyyy-mm-dd";
addCol(tInv, "VendorKey", DataType.Int64, true, false);
addCol(tInv, "CostCenterKey", DataType.Int64, true, false);
addCol(tInv, "GLAccountKey", DataType.Int64, true, false);
addCol(tInv, "ApproverKey", DataType.Int64, true, false);
addCol(tInv, "DocumentType", DataType.String, false, true);
addCol(tInv, "PaymentStatus", DataType.String, false, true);
addCol(tInv, "MatchStatus", DataType.String, false, true);
addCol(tInv, "Quantity", DataType.Int64, true, false);
addCol(tInv, "UnitPrice", DataType.Decimal, true, false);
addCol(tInv, "LineAmount", DataType.Decimal, true, false);
addCol(tInv, "TaxAmount", DataType.Decimal, true, false);
addCol(tInv, "NetAmount", DataType.Decimal, true, false);
addCol(tInv, "EarlyPayDiscountRate", DataType.Decimal, true, false);
addCol(tInv, "DaysToPay", DataType.Int64, true, false);
addCol(tInv, "IsPaidOnTime", DataType.Int64, true, false);

setM(tInv, "FactAPInvoices", @"let
    Source = Csv.Document(File.Contents(DataFolder & ""\FactAPInvoices.csv""), [Delimiter="","", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    #""Promoted Headers"" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #""Changed Type"" = Table.TransformColumnTypes(#""Promoted Headers"", {
        {""InvoiceID"", type text},
        {""LineNumber"", Int64.Type},
        {""PostingDateKey"", Int64.Type},
        {""PostingDate"", type date},
        {""DueDate"", type date},
        {""VendorKey"", Int64.Type},
        {""CostCenterKey"", Int64.Type},
        {""GLAccountKey"", Int64.Type},
        {""ApproverKey"", Int64.Type},
        {""DocumentType"", type text},
        {""PaymentStatus"", type text},
        {""MatchStatus"", type text},
        {""Quantity"", Int64.Type},
        {""UnitPrice"", Currency.Type},
        {""LineAmount"", Currency.Type},
        {""TaxAmount"", Currency.Type},
        {""NetAmount"", Currency.Type},
        {""EarlyPayDiscountRate"", Currency.Type},
        {""DaysToPay"", Int64.Type},
        {""IsPaidOnTime"", Int64.Type}
    }, ""en-US"")
in
    #""Changed Type""");

// 7. FactAPBudget
var tBud = Model.AddTable("FactAPBudget");
tBud.Description = "Monthly AP operating expense budget by CostCenter and GLAccount.";
var cBudPostDt = addCol(tBud, "PostingDate", DataType.DateTime, true, false);
cBudPostDt.FormatString = "yyyy-mm-dd";
addCol(tBud, "PostingDateKey", DataType.Int64, true, false);
addCol(tBud, "CostCenterKey", DataType.Int64, true, false);
addCol(tBud, "GLAccountKey", DataType.Int64, true, false);
addCol(tBud, "BudgetAmount", DataType.Decimal, true, false);

setM(tBud, "FactAPBudget", @"let
    Source = Csv.Document(File.Contents(DataFolder & ""\FactAPBudget.csv""), [Delimiter="","", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    #""Promoted Headers"" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #""Changed Type"" = Table.TransformColumnTypes(#""Promoted Headers"", {
        {""PostingDate"", type date},
        {""PostingDateKey"", Int64.Type},
        {""CostCenterKey"", Int64.Type},
        {""GLAccountKey"", Int64.Type},
        {""BudgetAmount"", Currency.Type}
    }, ""en-US"")
in
    #""Changed Type""");

// 8. _Measures
var tMeas = Model.AddTable("_Measures");
tMeas.Description = "Dedicated display table for corporate AP spend KPI measures.";
addCol(tMeas, "_Placeholder", DataType.String, true, false);
setM(tMeas, "_Measures", @"let
    Source = #table(type table [_Placeholder = nullable text], {})
in
    Source");

Action<string, string, string, string, string> addKpi = (mName, folder, fmt, desc, dax) =>
{
    var m = tMeas.AddMeasure(mName, dax);
    m.DisplayFolder = folder;
    m.FormatString = fmt;
    m.Description = desc;
};

addKpi(
    "Gross Invoice Spend",
    "01 AP Spend & Credits",
    "$#,##0.00",
    "Sum of NetAmount across standard Invoice documents before Credit Memo offsets.",
    "VAR _Net = [Net AP Spend]\nVAR _Gross = CALCULATE ( SUM ( 'FactAPInvoices'[NetAmount] ), 'FactAPInvoices'[DocumentType] = \"Invoice\" )\nRETURN\n    IF ( NOT ISBLANK ( _Net ), COALESCE ( _Gross, 0 ) )"
);

addKpi(
    "Credit Memo Amount",
    "01 AP Spend & Credits",
    "$#,##0.00",
    "Signed sum of negative NetAmount across Credit Memo lines.",
    "VAR _Net = [Net AP Spend]\nVAR _Credits = CALCULATE ( SUM ( 'FactAPInvoices'[NetAmount] ), 'FactAPInvoices'[DocumentType] = \"Credit Memo\" )\nRETURN\n    IF ( NOT ISBLANK ( _Net ), COALESCE ( _Credits, 0 ) )"
);

addKpi(
    "Net AP Spend",
    "01 AP Spend & Credits",
    "$#,##0.00",
    "Total net accounts payable spend combining Invoices and Credit Memos.",
    "SUM ( 'FactAPInvoices'[NetAmount] )"
);

addKpi(
    "Pre-Tax Spend",
    "01 AP Spend & Credits",
    "$#,##0.00",
    "Total net pre-tax AP line amount across Invoices and Credit Memos.",
    "SUM ( 'FactAPInvoices'[LineAmount] )"
);

addKpi(
    "Tax Spend",
    "01 AP Spend & Credits",
    "$#,##0.00",
    "Total net tax amount across AP invoice and credit memo lines.",
    "SUM ( 'FactAPInvoices'[TaxAmount] )"
);

addKpi(
    "Invoice Count",
    "01 AP Spend & Credits",
    "#,##0",
    "Distinct count of AP InvoiceID documents in current filter context.",
    "DISTINCTCOUNT ( 'FactAPInvoices'[InvoiceID] )"
);

addKpi(
    "Average Invoice Value",
    "01 AP Spend & Credits",
    "$#,##0.00",
    "Net AP Spend divided safely by distinct Invoice Count.",
    "VAR _Invoices = [Invoice Count]\nRETURN\n    DIVIDE ( [Net AP Spend], _Invoices )"
);

addKpi(
    "Credit Memo Rate %",
    "01 AP Spend & Credits",
    "0.00%",
    "Absolute Credit Memo Amount expressed as a share of Gross Invoice Spend.",
    "DIVIDE ( ABS ( [Credit Memo Amount] ), [Gross Invoice Spend] )"
);

addKpi(
    "Net Spend PY",
    "02 Time Intelligence",
    "$#,##0.00",
    "Prior-year Net AP Spend shifted via SAMEPERIODLASTYEAR on DimDate[Date].",
    "CALCULATE ( [Net AP Spend], SAMEPERIODLASTYEAR ( 'DimDate'[Date] ) )"
);

addKpi(
    "Net Spend YoY",
    "02 Time Intelligence",
    "$#,##0.00",
    "Year-over-year dollar change in Net AP Spend versus Net Spend PY.",
    "VAR _Current = [Net AP Spend]\nVAR _Prior = [Net Spend PY]\nRETURN\n    IF ( NOT ISBLANK ( _Current ) && NOT ISBLANK ( _Prior ), _Current - _Prior )"
);

addKpi(
    "Net Spend YoY %",
    "02 Time Intelligence",
    "0.00%",
    "Year-over-year percentage change in Net AP Spend over prior year.",
    "VAR _Prior = [Net Spend PY]\nRETURN\n    DIVIDE ( [Net Spend YoY], _Prior )"
);

addKpi(
    "Net Spend YTD",
    "02 Time Intelligence",
    "$#,##0.00",
    "Fiscal/calendar year-to-date cumulative Net AP Spend.",
    "VAR _LastActualDateKey = CALCULATE ( MAX ( 'FactAPInvoices'[PostingDateKey] ), REMOVEFILTERS () )\nVAR _FirstSelectedDateKey = MIN ( 'DimDate'[DateKey] )\nRETURN\n    IF (\n        NOT ISBLANK ( _LastActualDateKey ) && _FirstSelectedDateKey <= _LastActualDateKey,\n        CALCULATE ( [Net AP Spend], DATESYTD ( 'DimDate'[Date] ) )\n    )"
);

addKpi(
    "Budget Spend",
    "03 Budget & Variance",
    "$#,##0.00",
    "Monthly OPEX budget allocation from FactAPBudget at CostCenter x GLAccount grain.",
    "VAR _InvalidGrain = ISCROSSFILTERED ( 'DimVendor' ) || ISCROSSFILTERED ( 'DimApprover' )\nRETURN\n    IF ( NOT _InvalidGrain, SUM ( 'FactAPBudget'[BudgetAmount] ) )"
);

addKpi(
    "Budget Variance",
    "03 Budget & Variance",
    "$#,##0.00",
    "Remaining budget headroom (Budget Spend minus Net AP Spend) when both exist.",
    "VAR _Actual = [Net AP Spend]\nVAR _Budget = [Budget Spend]\nRETURN\n    IF ( NOT ISBLANK ( _Actual ) && NOT ISBLANK ( _Budget ), _Budget - _Actual )"
);

addKpi(
    "Budget Variance %",
    "03 Budget & Variance",
    "0.00%",
    "Budget Variance divided safely by Budget Spend.",
    "DIVIDE ( [Budget Variance], [Budget Spend] )"
);

addKpi(
    "Budget Utilization Status",
    "03 Budget & Variance",
    "@",
    "Attainment classification returning Under Budget, Over Budget, No Actuals, or No Budget.",
    "VAR _Actual = [Net AP Spend]\nVAR _Budget = [Budget Spend]\nRETURN\n    SWITCH (\n        TRUE (),\n        ISBLANK ( _Budget ) || _Budget = 0, \"No Budget\",\n        ISBLANK ( _Actual ), \"No Actuals\",\n        _Actual <= _Budget, \"Under Budget\",\n        \"Over Budget\"\n    )"
);

addKpi(
    "On-Time Payment %",
    "04 Payment & Controls",
    "0.00%",
    "Share of Paid invoice lines settled on or before vendor payment terms due date.",
    "VAR _PaidLines = CALCULATE ( COUNTROWS ( 'FactAPInvoices' ), 'FactAPInvoices'[PaymentStatus] = \"Paid\" )\nVAR _OnTimeLines = CALCULATE ( SUM ( 'FactAPInvoices'[IsPaidOnTime] ), 'FactAPInvoices'[PaymentStatus] = \"Paid\" )\nRETURN\n    DIVIDE ( _OnTimeLines, _PaidLines )"
);

addKpi(
    "Overdue Exposure",
    "04 Payment & Controls",
    "$#,##0.00",
    "Net AP Spend across invoice lines currently in Overdue payment status.",
    "CALCULATE ( SUM ( 'FactAPInvoices'[NetAmount] ), 'FactAPInvoices'[PaymentStatus] = \"Overdue\" )"
);

addKpi(
    "Three-Way Match %",
    "04 Payment & Controls",
    "0.00%",
    "Percentage of AP invoice lines verified via full 3-Way Matched controls.",
    "VAR _TotalLines = COUNTROWS ( 'FactAPInvoices' )\nVAR _MatchedLines = CALCULATE ( COUNTROWS ( 'FactAPInvoices' ), 'FactAPInvoices'[MatchStatus] = \"3-Way Matched\" )\nRETURN\n    DIVIDE ( _MatchedLines, _TotalLines )"
);

addKpi(
    "Exception Rate %",
    "04 Payment & Controls",
    "0.00%",
    "Share of AP invoice lines flagged with MatchStatus Exception controls.",
    "VAR _TotalLines = COUNTROWS ( 'FactAPInvoices' )\nVAR _ExceptionLines = CALCULATE ( COUNTROWS ( 'FactAPInvoices' ), 'FactAPInvoices'[MatchStatus] = \"Exception\" )\nRETURN\n    DIVIDE ( _ExceptionLines, _TotalLines )"
);

// 9. Relationships (pure 1:* Single)
Action<Table, string, Table, string> addRel = (fromTbl, fromCol, toTbl, toCol) =>
{
    var rel = Model.AddRelationship();
    rel.FromColumn = fromTbl.Columns[fromCol];
    rel.ToColumn = toTbl.Columns[toCol];
    rel.FromCardinality = RelationshipEndCardinality.Many;
    rel.ToCardinality = RelationshipEndCardinality.One;
    rel.CrossFilteringBehavior = CrossFilteringBehavior.OneDirection;
    rel.SecurityFilteringBehavior = SecurityFilteringBehavior.OneDirection;
    rel.IsActive = true;
};

addRel(tInv, "PostingDateKey", tDate, "DateKey");
addRel(tInv, "VendorKey", tVendor, "VendorKey");
addRel(tInv, "CostCenterKey", tCC, "CostCenterKey");
addRel(tInv, "GLAccountKey", tGL, "GLAccountKey");
addRel(tInv, "ApproverKey", tApp, "ApproverKey");
addRel(tBud, "PostingDateKey", tDate, "DateKey");
addRel(tBud, "CostCenterKey", tCC, "CostCenterKey");
addRel(tBud, "GLAccountKey", tGL, "GLAccountKey");

// 10. Fail-Closed RLS Roles
Action<string, string, Table, string> addStaticRole = (roleName, desc, tbl, expr) =>
{
    var role = Model.AddRole(roleName);
    role.Description = desc;
    role.ModelPermission = ModelPermission.Read;
    role.AddOrGetTablePermission(tbl).FilterExpression = expr;
};

addStaticRole("BU_NorthAmerica", "Static RLS isolating North America Business Unit.", tCC, "'DimCostCenter'[BusinessUnit] = \"North America\"");
addStaticRole("BU_Europe", "Static RLS isolating Europe Business Unit.", tCC, "'DimCostCenter'[BusinessUnit] = \"Europe\"");
addStaticRole("BU_APAC", "Static RLS isolating APAC Business Unit.", tCC, "'DimCostCenter'[BusinessUnit] = \"APAC\"");

var rDyn = Model.AddRole("Dynamic_Approver");
rDyn.Description = "Dynamic fail-closed RLS filtering AP invoices by approver UPN and denying budget access.";
rDyn.ModelPermission = ModelPermission.Read;
rDyn.AddOrGetTablePermission(tApp).FilterExpression = "VAR _Custom = CUSTOMDATA ()\nVAR _Identity = IF ( _Custom <> \"\", _Custom, USERPRINCIPALNAME () )\nRETURN\n    'DimApprover'[UserPrincipalName] = _Identity";
rDyn.AddOrGetTablePermission(tBud).FilterExpression = "FALSE ()";

foreach (var ds in Model.DataSources.ToList()) ds.Delete();

Info("Built CorporateFinanceAP model: tables=" + Model.Tables.Count + ", measures=" + Model.AllMeasures.Count() + ", rels=" + Model.Relationships.Count + ", roles=" + Model.Roles.Count);
