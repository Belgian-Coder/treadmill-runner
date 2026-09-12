using TreadmillRunner.Core.Bluetooth;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Infrastructure.Bluetooth;

namespace TreadmillRunner.IntegrationTests;

public sealed class HeartRateReconnectResolverTests
{
  private static readonly Guid PolarService =
    Guid.Parse("0000feee-0000-1000-8000-00805f9b34fb");
  private static readonly Guid HeartRateService =
    Guid.Parse("0000180d-0000-1000-8000-00805f9b34fb");

  [Fact]
  public void Prefers_the_current_device_id_over_name_and_family_fallbacks()
  {
    DeviceEnrollment enrollment = HeartRate(
      "102030405060",
      "Garmin Forerunner 965",
      HeartRateDeviceKind.Watch,
      HeartRateDeviceFamily.Garmin);
    var resolver = new HeartRateReconnectResolver();
    resolver.Observe(Advertisement(enrollment.DeviceId, "Different name"));
    resolver.Observe(Advertisement("AABBCCDDEEFF", enrollment.DisplayName));

    HeartRateReconnectResolution? resolution = resolver.Resolve(
      enrollment,
      enrollment.DeviceId,
      allowDisplayNameFallback: true,
      allowFamilyFallback: true);

    Assert.NotNull(resolution);
    Assert.Equal(enrollment.DeviceId, resolution.DeviceId);
    Assert.Equal(HeartRateReconnectMatch.ExactDeviceId, resolution.Match);
  }

  [Fact]
  public void Merges_split_advertisements_before_resolving_an_exact_name()
  {
    DeviceEnrollment enrollment = HeartRate(
      "102030405060",
      "Polar H10",
      HeartRateDeviceKind.ChestStrap,
      HeartRateDeviceFamily.Polar);
    var resolver = new HeartRateReconnectResolver();
    resolver.Observe(new BleAdvertisement("AABBCCDDEEFF", null, -48, [HeartRateService]));
    resolver.Observe(new BleAdvertisement("AABBCCDDEEFF", "Polar H10", -44, []));

    HeartRateReconnectResolution? resolution = resolver.Resolve(
      enrollment,
      enrollment.DeviceId,
      allowDisplayNameFallback: true,
      allowFamilyFallback: true);

    Assert.NotNull(resolution);
    Assert.Equal("AABBCCDDEEFF", resolution.DeviceId);
    Assert.Equal(HeartRateReconnectMatch.ExactDisplayName, resolution.Match);
  }

  [Fact]
  public void Uses_a_unique_matching_family_and_kind_for_a_custom_display_name()
  {
    DeviceEnrollment enrollment = HeartRate(
      "102030405060",
      "Basement heart-rate strap",
      HeartRateDeviceKind.ChestStrap,
      HeartRateDeviceFamily.Polar);
    var resolver = new HeartRateReconnectResolver();
    resolver.Observe(Advertisement("AABBCCDDEEFF", "Polar H10 12345678"));

    HeartRateReconnectResolution? resolution = resolver.Resolve(
      enrollment,
      enrollment.DeviceId,
      allowDisplayNameFallback: true,
      allowFamilyFallback: true);

    Assert.NotNull(resolution);
    Assert.Equal("AABBCCDDEEFF", resolution.DeviceId);
    Assert.Equal(HeartRateReconnectMatch.UniqueFamilyAndKind, resolution.Match);
  }

  [Fact]
  public void Reclassifies_generic_stored_metadata_after_a_product_specific_rename()
  {
    DeviceEnrollment enrollment = HeartRate(
      "102030405060",
      "Marc Polar H10",
      HeartRateDeviceKind.Sensor,
      HeartRateDeviceFamily.Other);
    var resolver = new HeartRateReconnectResolver();
    resolver.Observe(Advertisement("AABBCCDDEEFF", "Polar H10"));

    HeartRateReconnectResolution? resolution = resolver.Resolve(
      enrollment,
      enrollment.DeviceId,
      allowDisplayNameFallback: true,
      allowFamilyFallback: true);

    Assert.NotNull(resolution);
    Assert.Equal("AABBCCDDEEFF", resolution.DeviceId);
    Assert.Equal(HeartRateReconnectMatch.UniqueFamilyAndKind, resolution.Match);
  }

  [Fact]
  public void Refuses_an_ambiguous_family_match()
  {
    DeviceEnrollment enrollment = HeartRate(
      "102030405060",
      "My watch",
      HeartRateDeviceKind.Watch,
      HeartRateDeviceFamily.Garmin);
    var resolver = new HeartRateReconnectResolver();
    resolver.Observe(Advertisement("AABBCCDDEEFF", "Garmin Forerunner 965"));
    resolver.Observe(Advertisement("112233445566", "Venu 3"));

    HeartRateReconnectResolution? resolution = resolver.Resolve(
      enrollment,
      enrollment.DeviceId,
      allowDisplayNameFallback: true,
      allowFamilyFallback: true);

    Assert.Null(resolution);
  }

  [Fact]
  public void Refuses_family_only_rebinding_without_advertised_heart_rate_evidence()
  {
    DeviceEnrollment enrollment = HeartRate(
      "102030405060",
      "My Garmin watch",
      HeartRateDeviceKind.Watch,
      HeartRateDeviceFamily.Garmin);
    var resolver = new HeartRateReconnectResolver();
    resolver.Observe(new BleAdvertisement(
      "AABBCCDDEEFF",
      "Garmin Edge 1040",
      -42,
      []));

    HeartRateReconnectResolution? resolution = resolver.Resolve(
      enrollment,
      enrollment.DeviceId,
      allowDisplayNameFallback: true,
      allowFamilyFallback: true);

    Assert.Null(resolution);
  }

  [Fact]
  public void Refuses_a_garmin_cycling_computer_even_when_it_advertises_heart_rate()
  {
    DeviceEnrollment enrollment = HeartRate(
      "102030405060",
      "My Garmin watch",
      HeartRateDeviceKind.Watch,
      HeartRateDeviceFamily.Garmin);
    var resolver = new HeartRateReconnectResolver();
    resolver.Observe(Advertisement("AABBCCDDEEFF", "Garmin Edge 1040"));

    HeartRateReconnectResolution? resolution = resolver.Resolve(
      enrollment,
      enrollment.DeviceId,
      allowDisplayNameFallback: true,
      allowFamilyFallback: true);

    Assert.Null(resolution);
  }

  [Fact]
  public void Refuses_exact_name_rebinding_without_advertised_heart_rate_evidence()
  {
    DeviceEnrollment enrollment = HeartRate(
      "102030405060",
      "Garmin Forerunner 965",
      HeartRateDeviceKind.Watch,
      HeartRateDeviceFamily.Garmin);
    var resolver = new HeartRateReconnectResolver();
    resolver.Observe(new BleAdvertisement(
      "AABBCCDDEEFF",
      enrollment.DisplayName,
      -42,
      []));

    HeartRateReconnectResolution? resolution = resolver.Resolve(
      enrollment,
      enrollment.DeviceId,
      allowDisplayNameFallback: true,
      allowFamilyFallback: true);

    Assert.Null(resolution);
  }

  [Fact]
  public void Can_exclude_a_failed_current_locator_and_select_its_unique_replacement()
  {
    DeviceEnrollment enrollment = HeartRate(
      "102030405060",
      "Garmin Forerunner 965",
      HeartRateDeviceKind.Watch,
      HeartRateDeviceFamily.Garmin);
    var resolver = new HeartRateReconnectResolver();
    resolver.Observe(Advertisement(enrollment.DeviceId, enrollment.DisplayName));
    resolver.Observe(Advertisement("AABBCCDDEEFF", enrollment.DisplayName));

    HeartRateReconnectResolution? resolution = resolver.Resolve(
      enrollment,
      enrollment.DeviceId,
      allowDisplayNameFallback: true,
      allowFamilyFallback: true,
      excludedDeviceId: enrollment.DeviceId);

    Assert.NotNull(resolution);
    Assert.Equal("AABBCCDDEEFF", resolution.DeviceId);
    Assert.Equal(HeartRateReconnectMatch.ExactDisplayName, resolution.Match);
  }

  [Fact]
  public async Task Polar_memory_locator_adopts_a_unique_fresh_rotating_address()
  {
    DeviceEnrollment enrollment = HeartRate(
      "102030405060",
      "Polar heart-rate sensor",
      HeartRateDeviceKind.ChestStrap,
      HeartRateDeviceFamily.Polar);
    var broker = new AdvertisementBroker(
      new BleAdvertisement("AABBCCDDEEFF", null, -45, [PolarService]),
      new BleAdvertisement("AABBCCDDEEFF", "Polar H10", -44, [HeartRateService]));
    var locator = new PolarH10ConnectionLocator(broker, TimeSpan.FromMilliseconds(25));

    string resolved = await locator.ResolveAsync(enrollment, [enrollment]);

    Assert.Equal("AABBCCDDEEFF", resolved);
  }

  [Fact]
  public async Task Polar_memory_locator_fails_closed_when_another_enrolled_Polar_strap_makes_family_ambiguous()
  {
    DeviceEnrollment enrollment = HeartRate(
      "102030405060",
      "Primary chest strap",
      HeartRateDeviceKind.ChestStrap,
      HeartRateDeviceFamily.Polar);
    DeviceEnrollment peer = HeartRate(
      "112233445566",
      "Spare chest strap",
      HeartRateDeviceKind.ChestStrap,
      HeartRateDeviceFamily.Polar);
    var broker = new AdvertisementBroker(
      new BleAdvertisement("AABBCCDDEEFF", "Polar H10", -44, [HeartRateService, PolarService]));
    var locator = new PolarH10ConnectionLocator(broker, TimeSpan.FromMilliseconds(25));

    string resolved = await locator.ResolveAsync(enrollment, [enrollment, peer]);

    Assert.Equal(enrollment.DeviceId, resolved);
  }

  private static BleAdvertisement Advertisement(string deviceId, string name) =>
    new(deviceId, name, -42, [HeartRateService]);

  private static DeviceEnrollment HeartRate(
    string deviceId,
    string displayName,
    HeartRateDeviceKind kind,
    HeartRateDeviceFamily family) => new(
      Guid.NewGuid(),
      DeviceRole.HeartRate,
      deviceId,
      "bluetooth-heart-rate",
      new string('b', 64),
      displayName,
      null,
      null,
      null,
      null,
      TreadmillCapabilityEvidence.Unknown,
      null,
      kind,
      family);

  private sealed class AdvertisementBroker(params BleAdvertisement[] advertisements) : IBleAdvertisementBroker
  {
    public async IAsyncEnumerable<BleAdvertisement> ScanAsync(
      [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
      foreach (BleAdvertisement advertisement in advertisements)
      {
        cancellationToken.ThrowIfCancellationRequested();
        yield return advertisement;
      }
      await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
    }
  }
}
