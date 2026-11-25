var tableCount = Model.Tables.Count;
var measureCount = Model.AllMeasures.Count();
var relationshipCount = Model.Relationships.Count;
var roleCount = Model.Roles.Count;
var dateTables = Model.Tables.Where(t => t.DataCategory == "Time").Select(t => t.Name).ToList();
var bidirRels = Model.Relationships.Where(r => r.CrossFilteringBehavior == CrossFilteringBehavior.BothDirections).Count();
var hiddenFkCols = Model.Relationships.Where(r => r.FromColumn != null && !r.FromColumn.IsHidden).Count();

Func<string, string> esc = s => (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");

var dateTableJson = "[" + string.Join(",", dateTables.Select(d => "\"" + esc(d) + "\"")) + "]";
var json = "{"
    + "\"model_name\":\"" + esc(Model.Database != null ? Model.Database.Name : "SemanticModel") + "\","
    + "\"table_count\":" + tableCount + ","
    + "\"measure_count\":" + measureCount + ","
    + "\"relationship_count\":" + relationshipCount + ","
    + "\"role_count\":" + roleCount + ","
    + "\"date_tables\":" + dateTableJson + ","
    + "\"bidirectional_relationships\":" + bidirRels + ","
    + "\"unhidden_fk_columns\":" + hiddenFkCols
    + "}";

Info("MODEL_AUDIT_JSON:" + json);
