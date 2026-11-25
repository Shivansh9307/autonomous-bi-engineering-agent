var count = Model.AllMeasures.Count();
try
{
    Model.AllMeasures.FormatDax();
    Info("Formatted measures: " + count);
}
catch (Exception ex)
{
    Warning("DAX formatting skipped or failed (offline/network unavailable): " + ex.Message);
    Info("Formatted measures check complete (count=" + count + ")");
}
