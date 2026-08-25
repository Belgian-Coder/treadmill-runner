using System.Net;
using System.Net.Http.Json;

namespace TreadmillRunner.Web.Planning;

public sealed record LocalTrendView(
  Guid ProfileId,
  int CompletedSessions,
  TimeSpan Duration,
  double DistanceKilometers,
  int IncompleteTelemetrySessions,
  double LongestDistanceKilometers,
  TimeSpan LongestDuration,
  int? HighestAverageHeartRateBpm);

public sealed record LocalGoalView(
  Guid Id,
  Guid ProfileId,
  string Kind,
  string Period,
  double TargetValue,
  bool Enabled,
  int Version,
  DateTimeOffset UpdatedAtUtc);

public sealed record LocalInsightsView(
  LocalTrendView Trends,
  LocalTrendView WeeklyTrends,
  LocalTrendView MonthlyTrends,
  IReadOnlyList<LocalGoalView> Goals);

public sealed record LocalGoalUpdate(
  Guid? Id,
  string Kind,
  string Period,
  double TargetValue,
  bool Enabled,
  int? ExpectedVersion);

public sealed record LocalApiResult<T>(T? Value, HttpStatusCode StatusCode)
{
  public bool IsSuccess => (int)StatusCode is >= 200 and < 300 && Value is not null;
}

public sealed class LocalFirstApiClient(HttpClient http)
{
  public Task<LocalInsightsView?> GetInsightsAsync(Guid profileId, CancellationToken cancellationToken = default) =>
    http.GetFromJsonAsync<LocalInsightsView>($"api/local-first/profiles/{profileId:D}/insights", cancellationToken);

  public async Task<LocalApiResult<LocalGoalView>> SaveGoalAsync(
    Guid profileId,
    LocalGoalUpdate update,
    CancellationToken cancellationToken = default)
  {
    using HttpResponseMessage response = await http.PutAsJsonAsync(
      $"api/local-first/profiles/{profileId:D}/goals", update, cancellationToken);
    LocalGoalView? value = response.IsSuccessStatusCode
      ? await response.Content.ReadFromJsonAsync<LocalGoalView>(cancellationToken)
      : null;
    return new(value, response.StatusCode);
  }
}
