namespace TreadmillRunner.Web.Live;

public sealed record ChartInspectionSeries(
    string Group,
    string Label,
    string Unit,
    int DecimalPlaces,
    string LegendClass);

public sealed record ChartInspectionPoint(
    TimeSpan Elapsed,
    double X,
    IReadOnlyList<double?> Values);
