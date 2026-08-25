namespace TreadmillRunner.Core.Devices;

public sealed record HeartRateDeviceAssignment(
  Guid Id,
  Guid UserProfileId,
  Guid DeviceEnrollmentId,
  int Priority,
  bool AutoConnect,
  bool IsPreferred,
  int Version)
{
  public HeartRateDeviceAssignment Validate()
  {
    if (Id == Guid.Empty) throw new ArgumentException("Assignment ID cannot be empty.", nameof(Id));
    if (UserProfileId == Guid.Empty) throw new ArgumentException("Profile ID cannot be empty.", nameof(UserProfileId));
    if (DeviceEnrollmentId == Guid.Empty) throw new ArgumentException("Enrollment ID cannot be empty.", nameof(DeviceEnrollmentId));
    if (Priority is < 0 or > 99) throw new ArgumentOutOfRangeException(nameof(Priority), "Priority must be between 0 and 99.");
    if (Version < 1) throw new ArgumentOutOfRangeException(nameof(Version));
    return this;
  }
}

public enum HeartRateSignalQuality
{
  Unavailable,
  Valid,
  ContactLost,
  Invalid,
}

public enum HeartRateContactState
{
  Unknown,
  NotSupported,
  Detected,
  NotDetected,
}

public sealed record HeartRateSourceSnapshot(
  Guid EnrollmentId,
  string DisplayName,
  HeartRateDeviceKind Kind,
  HeartRateDeviceFamily Family,
  DeviceConnectionState State,
  long ConnectionGeneration,
  ushort? BeatsPerMinute,
  DateTimeOffset? ObservedAt,
  string? Fault,
  byte? BatteryPercent = null,
  DateTimeOffset? BatteryObservedAt = null,
  HeartRateSignalQuality Quality = HeartRateSignalQuality.Unavailable,
  HeartRateContactState ContactState = HeartRateContactState.Unknown)
{
  public bool IsFresh(DateTimeOffset now, TimeSpan freshnessLimit) =>
    State == DeviceConnectionState.Ready &&
    Quality == HeartRateSignalQuality.Valid &&
    BeatsPerMinute is >= 30 and <= 250 &&
    ObservedAt is not null &&
    now - ObservedAt.Value >= TimeSpan.Zero &&
    now - ObservedAt.Value <= freshnessLimit;
}

public static class HeartRateSourceSelector
{
  public static HeartRateSourceSnapshot? Select(
    IReadOnlyCollection<HeartRateSourceSnapshot> sources,
    IReadOnlyCollection<HeartRateDeviceAssignment> assignments,
    Guid? profileId,
    DateTimeOffset now,
    TimeSpan freshnessLimit)
  {
    IEnumerable<(HeartRateSourceSnapshot Source, HeartRateDeviceAssignment? Assignment)> eligible;
    if (profileId is null)
    {
      eligible = sources.Select(source =>
        (source, assignments.Where(item => item.DeviceEnrollmentId == source.EnrollmentId &&
            (item.AutoConnect || source.Family == HeartRateDeviceFamily.Polar))
          .OrderBy(item => item.Priority).FirstOrDefault()));
    }
    else
    {
      HeartRateDeviceAssignment[] profileAssignments = assignments
        .Where(assignment => assignment.UserProfileId == profileId &&
          (assignment.AutoConnect || sources.Any(source =>
            source.EnrollmentId == assignment.DeviceEnrollmentId && source.Family == HeartRateDeviceFamily.Polar)))
        .ToArray();
      eligible = profileAssignments.Length > 0
        ? from assignment in profileAssignments
          join source in sources on assignment.DeviceEnrollmentId equals source.EnrollmentId
          select (source, (HeartRateDeviceAssignment?)assignment)
        : sources
          .Where(source => assignments.All(assignment => assignment.DeviceEnrollmentId != source.EnrollmentId))
          .Select(source => (source, (HeartRateDeviceAssignment?)null));
    }

    return eligible
      .Where(item => item.Source.IsFresh(now, freshnessLimit))
      .OrderBy(item => FamilyTier(item.Source))
      .ThenByDescending(item => item.Assignment?.IsPreferred == true)
      .ThenBy(item => item.Assignment?.Priority ?? 99)
      .ThenBy(item => item.Source.EnrollmentId)
      .Select(item => item.Source)
      .FirstOrDefault();
  }

  private static int FamilyTier(HeartRateSourceSnapshot source) => source.Family switch
  {
    HeartRateDeviceFamily.Polar => 0,
    _ when source.Kind == HeartRateDeviceKind.ChestStrap => 1,
    HeartRateDeviceFamily.Garmin => 2,
    _ when source.Kind == HeartRateDeviceKind.Watch => 3,
    _ => 4,
  };
}
