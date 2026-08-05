using TreadmillRunner.Core.Devices;

namespace TreadmillRunner.Core.Tests;

public sealed class HeartRateSourceSelectorTests
{
  [Fact]
  public void Fresh_polar_outranks_a_preferred_garmin_watch()
  {
    DateTimeOffset now = new(2026, 8, 4, 12, 0, 0, TimeSpan.Zero);
    Guid polarId = Guid.NewGuid();
    Guid garminId = Guid.NewGuid();
    HeartRateSourceSnapshot polar = new(polarId, "Polar H10", HeartRateDeviceKind.ChestStrap, HeartRateDeviceFamily.Polar, DeviceConnectionState.Ready, 1, 130, now, null);
    HeartRateSourceSnapshot garmin = new(garminId, "Garmin fēnix 8", HeartRateDeviceKind.Watch, HeartRateDeviceFamily.Garmin, DeviceConnectionState.Ready, 1, 131, now, null);
    Guid profileId = Guid.NewGuid();
    HeartRateDeviceAssignment[] assignments =
    [
      Assignment(profileId, polar.EnrollmentId, priority: 20, preferred: false, autoConnect: false),
      Assignment(profileId, garmin.EnrollmentId, priority: 0, preferred: true, autoConnect: true),
    ];

    HeartRateSourceSnapshot? selected = HeartRateSourceSelector.Select(
      [garmin, polar], assignments, profileId, now, TimeSpan.FromSeconds(5));

    Assert.Same(polar, selected);
  }
  private static readonly DateTimeOffset Now = new(2026, 8, 4, 10, 0, 0, TimeSpan.Zero);
  private static readonly Guid Marc = Guid.Parse("10000000-0000-0000-0000-000000000001");
  private static readonly Guid Wife = Guid.Parse("20000000-0000-0000-0000-000000000002");
  private static readonly Guid Polar = Guid.Parse("30000000-0000-0000-0000-000000000003");
  private static readonly Guid Fenix = Guid.Parse("40000000-0000-0000-0000-000000000004");
  private static readonly Guid Vivoactive = Guid.Parse("50000000-0000-0000-0000-000000000005");

  [Fact]
  public void Selects_explicit_preferred_polar_before_fresh_watch()
  {
    HeartRateSourceSnapshot? selected = HeartRateSourceSelector.Select(
      [Source(Polar, "Polar H10", HeartRateDeviceKind.ChestStrap, HeartRateDeviceFamily.Polar),
       Source(Fenix, "Garmin fēnix 8", HeartRateDeviceKind.Watch, HeartRateDeviceFamily.Garmin)],
      [Assignment(Marc, Polar, priority: 1, preferred: true), Assignment(Marc, Fenix, priority: 0)],
      Marc,
      Now,
      TimeSpan.FromSeconds(5));

    Assert.Equal(Polar, selected?.EnrollmentId);
  }

  [Fact]
  public void Falls_back_to_assigned_watch_when_polar_is_stale()
  {
    HeartRateSourceSnapshot? selected = HeartRateSourceSelector.Select(
      [Source(Polar, "Polar H10", HeartRateDeviceKind.ChestStrap, HeartRateDeviceFamily.Polar, ageSeconds: 5.1),
       Source(Fenix, "Garmin fēnix 8", HeartRateDeviceKind.Watch, HeartRateDeviceFamily.Garmin)],
      [Assignment(Marc, Polar, priority: 0, preferred: true), Assignment(Marc, Fenix, priority: 1)],
      Marc,
      Now,
      TimeSpan.FromSeconds(5));

    Assert.Equal(Fenix, selected?.EnrollmentId);
  }

  [Fact]
  public void Never_selects_another_runners_private_watch()
  {
    HeartRateSourceSnapshot? selected = HeartRateSourceSelector.Select(
      [Source(Vivoactive, "Garmin vívoactive", HeartRateDeviceKind.Watch, HeartRateDeviceFamily.Garmin)],
      [Assignment(Wife, Vivoactive, priority: 0, preferred: true)],
      Marc,
      Now,
      TimeSpan.FromSeconds(5));

    Assert.Null(selected);
  }

  [Fact]
  public void Unassigned_legacy_strap_is_shared_until_runner_preferences_are_configured()
  {
    HeartRateSourceSnapshot? selected = HeartRateSourceSelector.Select(
      [Source(Polar, "Polar H10", HeartRateDeviceKind.ChestStrap, HeartRateDeviceFamily.Polar)],
      [], Marc, Now, TimeSpan.FromSeconds(5));

    Assert.Equal(Polar, selected?.EnrollmentId);
  }

  [Fact]
  public void Five_second_boundary_is_fresh_and_polar_remains_automatic()
  {
    HeartRateSourceSnapshot source = Source(
      Polar,
      "Polar H10",
      HeartRateDeviceKind.ChestStrap,
      HeartRateDeviceFamily.Polar,
      ageSeconds: 5);
    Assert.Same(source, HeartRateSourceSelector.Select(
      [source], [Assignment(Marc, Polar, 0, preferred: true)], Marc, Now, TimeSpan.FromSeconds(5)));
    Assert.Same(source, HeartRateSourceSelector.Select(
      [source], [Assignment(Marc, Polar, 0, preferred: true, autoConnect: false)], Marc, Now, TimeSpan.FromSeconds(5)));
  }

  [Theory]
  [InlineData("Garmin Fenix 8")]
  [InlineData("Garmin fēnix 8")]
  [InlineData("Garmin Vivoactive 5")]
  [InlineData("Garmin vívoactive 6")]
  public void Classifier_persists_garmin_watch_identity(string name)
  {
    Assert.Equal(HeartRateDeviceKind.Watch, HeartRateDeviceClassifier.Classify(name));
    Assert.Equal(HeartRateDeviceFamily.Garmin, HeartRateDeviceClassifier.Family(name));
  }

  private static HeartRateSourceSnapshot Source(
    Guid id,
    string name,
    HeartRateDeviceKind kind,
    HeartRateDeviceFamily family,
    double ageSeconds = 0) => new(
      id,
      name,
      kind,
      family,
      DeviceConnectionState.Ready,
      1,
      132,
      Now.AddSeconds(-ageSeconds),
      null);

  private static HeartRateDeviceAssignment Assignment(
    Guid profileId,
    Guid enrollmentId,
    int priority,
    bool preferred = false,
    bool autoConnect = true) => new(
      Guid.NewGuid(), profileId, enrollmentId, priority, autoConnect, preferred, 1);
}
