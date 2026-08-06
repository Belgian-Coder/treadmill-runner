using System.Security.Cryptography;
using System.Text;

namespace TreadmillRunner.Core.Workouts;

public sealed record PremadePlanSessionTemplate(
  int Position,
  int WeekNumber,
  int SessionNumber,
  string Phase,
  string WorkoutKey,
  string WorkoutName,
  int DurationMinutes,
  double TargetSpeedKph,
  double TargetInclinePercent,
  int? HeartRateZoneNumber);

public sealed record PremadePlanTemplate(
  string Id,
  string Version,
  string Name,
  string Description,
  string Goal,
  string Experience,
  int Weeks,
  int SessionsPerWeek,
  bool Repeatable,
  bool RequiresHeartRate,
  IReadOnlySet<string> Tags,
  IReadOnlyList<PremadePlanSessionTemplate> Sessions)
{
  public int SessionCount => Sessions.Count;
  public int MaximumDurationMinutes => Sessions.Max(static session => session.DurationMinutes);
  public double MaximumSpeedKph => Sessions.Max(static session => session.TargetSpeedKph);
  public double MaximumInclinePercent => Sessions.Max(static session => session.TargetInclinePercent);
  public string ContentSha256 => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(string.Join(
    '|',
    Id,
    Version,
    Name,
    Weeks,
    SessionsPerWeek,
    string.Join(';', Sessions.Select(static session => $"{session.WeekNumber}:{session.SessionNumber}:{session.Phase}:{session.WorkoutKey}"))))));
}

public static class PremadePlanCatalog
{
  public const string CurrentVersion = "1.0.0";

  public static IReadOnlyList<PremadePlanTemplate> All { get; } =
  [
    Create("getting-started", "Getting Started", "A calm introduction to consistent treadmill sessions.", "General fitness", "Beginner", 4, 3, 22, 5.5, false, false, "general-fitness", "walking"),
    Create("first-5k", "First 5K", "Six weeks of easy running, controlled intervals, and a longer weekly session.", "5K", "Beginner", 6, 3, 30, 7.0, false, false, "5k"),
    Create("beginner-5k-standard", "Beginner 5K Standard", "A balanced nine-week 5K progression.", "5K", "Beginner", 9, 3, 34, 7.5, false, false, "5k"),
    Create("beginner-5k-progressive", "Beginner 5K Progressive", "Ten weeks with gradually longer work intervals.", "5K", "Beginner", 10, 3, 36, 8.0, false, false, "5k"),
    Create("beginner-5k-gentle", "Beginner 5K Gentle", "A slower fourteen-week route to continuous 5K running.", "5K", "Beginner", 14, 3, 38, 7.0, false, false, "5k", "gentle"),
    Create("heart-rate-5k-gentle", "Heart-rate 5K Gentle", "Zone-guided easy and aerobic work with bounded speed fallback.", "5K", "Beginner", 12, 3, 36, 7.5, false, true, "5k", "heart-rate", "gentle"),
    Create("5k-performance", "5K Performance", "Eight weeks of threshold, interval, and aerobic sessions for experienced runners.", "5K", "Experienced", 8, 4, 45, 11.0, false, false, "5k", "performance"),
    Create("first-10k", "First 10K", "Six weeks that extend an existing 5K base toward 10K.", "10K", "Intermediate", 6, 3, 48, 8.0, false, false, "10k"),
    Create("10k-builder-gentle", "10K Builder Gentle", "A conservative fourteen-week 10K progression.", "10K", "Intermediate", 14, 3, 55, 8.0, false, false, "10k", "gentle"),
    Create("heart-rate-10k-gentle", "Heart-rate 10K Gentle", "Zone-guided aerobic development with bounded speed fallback.", "10K", "Intermediate", 14, 3, 55, 8.5, false, true, "10k", "heart-rate", "gentle"),
    Create("10k-performance", "10K Performance", "Eight weeks of tempo, threshold, interval, and long aerobic sessions.", "10K", "Experienced", 8, 4, 65, 12.0, false, false, "10k", "performance"),
    Create("5k-to-10k-distance-first-58", "5K to 10K Distance First", "A long-horizon 58-week, 174-session distance-first progression.", "10K", "Beginner", 58, 3, 70, 9.0, false, false, "5k", "10k", "long-plan"),
    Create("general-treadmill-fitness", "General Treadmill Fitness", "Six weeks mixing easy endurance, steady work, and incline variety.", "General fitness", "All levels", 6, 3, 35, 8.0, false, false, "general-fitness"),
    Create("5k-maintenance", "5K Maintenance", "A repeatable four-week plan for maintaining a 5K routine.", "5K", "Intermediate", 4, 3, 38, 8.5, true, false, "5k", "maintenance"),
    Create("10k-maintenance", "10K Maintenance", "A repeatable four-week plan for maintaining a 10K routine.", "10K", "Intermediate", 4, 3, 55, 9.0, true, false, "10k", "maintenance"),
    Create("walking-and-recovery", "Walking and Recovery", "A repeatable four-week selection of comfortable walks and light incline variety.", "Walking", "All levels", 4, 3, 30, 5.5, true, false, "walking", "recovery"),
  ];

  public static PremadePlanTemplate Find(string id, string? version = null)
  {
    PremadePlanTemplate? template = All.SingleOrDefault(candidate =>
      string.Equals(candidate.Id, id, StringComparison.Ordinal) &&
      (version is null || string.Equals(candidate.Version, version, StringComparison.Ordinal)));
    return template ?? throw new KeyNotFoundException($"Premade plan '{id}' was not found.");
  }

  public static WorkoutDefinition BuildWorkout(PremadePlanSessionTemplate session)
  {
    ArgumentNullException.ThrowIfNull(session);
    int warmup = Math.Clamp(session.DurationMinutes / 6, 4, 8);
    int cooldown = Math.Clamp(session.DurationMinutes / 8, 4, 7);
    int main = Math.Max(5, session.DurationMinutes - warmup - cooldown);
    double warmupSpeed = Math.Min(4.5, session.TargetSpeedKph);
    SpeedDirective mainSpeed = session.HeartRateZoneNumber is { } zone
      ? new HeartRateZoneSpeed(
        zone,
        session.TargetSpeedKph,
        Math.Max(0.8, session.TargetSpeedKph - 1.5),
        session.TargetSpeedKph)
      : new FixedSpeed(session.TargetSpeedKph);
    return new WorkoutDefinition(
      1,
      session.WorkoutName,
      $"Premade plan workout · {session.Phase}",
      [
        new WorkoutStep(new TimeGoal(TimeSpan.FromMinutes(warmup)), new FixedSpeed(warmupSpeed), new FixedIncline(0.5), "Warm up"),
        new WorkoutStep(new TimeGoal(TimeSpan.FromMinutes(main)), mainSpeed, new FixedIncline(session.TargetInclinePercent), session.HeartRateZoneNumber is null ? "Steady effort" : $"Stay in Z{session.HeartRateZoneNumber}"),
        new WorkoutStep(new TimeGoal(TimeSpan.FromMinutes(cooldown)), new FixedSpeed(warmupSpeed), new FixedIncline(0.5), "Cool down"),
      ]);
  }

  private static PremadePlanTemplate Create(
    string id,
    string name,
    string description,
    string goal,
    string experience,
    int weeks,
    int sessionsPerWeek,
    int maximumDuration,
    double maximumSpeed,
    bool repeatable,
    bool heartRate,
    params string[] tags)
  {
    var sessions = new List<PremadePlanSessionTemplate>(weeks * sessionsPerWeek);
    for (int week = 1; week <= weeks; week++)
    {
      string phase = Phase(id, week, weeks);
      double progress = weeks == 1 ? 1 : (double)(week - 1) / (weeks - 1);
      for (int session = 1; session <= sessionsPerWeek; session++)
      {
        string type = session == sessionsPerWeek ? "Long" : session == 2 ? "Quality" : "Easy";
        int duration = Math.Max(15, (int)Math.Round(maximumDuration * (0.58 + progress * 0.42) - (session == 1 ? 4 : 0)));
        double speedFactor = session == 2 ? 1 : session == sessionsPerWeek ? 0.85 : 0.78;
        double speed = Math.Round(Math.Clamp(maximumSpeed * speedFactor + progress * maximumSpeed * (1 - speedFactor), 0.8, maximumSpeed) * 2) / 2;
        double incline = session == 2 && goal is "General fitness" or "Walking" ? 2 : 1;
        int? zone = heartRate ? (session == 2 ? 3 : 2) : null;
        string workoutKey = $"{type.ToLowerInvariant()}-{duration}-{speed:0.0}-{incline:0.0}-{zone?.ToString() ?? "fixed"}";
        sessions.Add(new PremadePlanSessionTemplate(
          sessions.Count + 1,
          week,
          session,
          phase,
          workoutKey,
          zone is null ? $"{type} {duration} min" : $"{type} Z{zone} · {duration} min",
          duration,
          speed,
          incline,
          zone));
      }
    }

    return new PremadePlanTemplate(
      id,
      CurrentVersion,
      name,
      description,
      goal,
      experience,
      weeks,
      sessionsPerWeek,
      repeatable,
      heartRate,
      new HashSet<string>(tags.Append(goal.ToLowerInvariant().Replace(' ', '-')), StringComparer.Ordinal),
      sessions);
  }

  private static string Phase(string id, int week, int weeks)
  {
    if (id == "5k-to-10k-distance-first-58")
    {
      return week switch
      {
        <= 12 => "Foundation",
        <= 26 => "5K base",
        <= 44 => "10K build",
        _ => "Distance consolidation",
      };
    }

    if (id.Contains("maintenance", StringComparison.Ordinal)) return "Maintenance cycle";
    double progress = (double)week / weeks;
    return progress switch
    {
      <= 0.3 => "Foundation",
      <= 0.75 => "Build",
      <= 0.9 => "Peak",
      _ => "Consolidate",
    };
  }
}
