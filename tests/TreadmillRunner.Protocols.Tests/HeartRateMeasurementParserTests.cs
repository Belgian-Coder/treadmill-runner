using TreadmillRunner.Protocols.HeartRate;

namespace TreadmillRunner.Protocols.Tests;

public sealed class HeartRateMeasurementParserTests
{
  [Fact]
  public void Parses_uint8_measurement()
  {
    var measurement = HeartRateMeasurementParser.Parse([0x00, 72]);

    Assert.Equal((ushort)72, measurement.BeatsPerMinute);
    Assert.Equal(HeartRateContactStatus.NotSupported, measurement.ContactStatus);
    Assert.Null(measurement.EnergyExpendedKilojoules);
    Assert.Empty(measurement.RrIntervals);
  }

  [Fact]
  public void Parses_uint16_contact_energy_and_rr_intervals()
  {
    // UInt16 HR, contact supported+detected, energy present, RR present.
    var measurement = HeartRateMeasurementParser.Parse(
        [0x1F, 0x2C, 0x01, 0x34, 0x12, 0x00, 0x04, 0x00, 0x02]);

    Assert.Equal((ushort)300, measurement.BeatsPerMinute);
    Assert.Equal(HeartRateContactStatus.Detected, measurement.ContactStatus);
    Assert.Equal((ushort)0x1234, measurement.EnergyExpendedKilojoules);
    Assert.Equal([TimeSpan.FromSeconds(1), TimeSpan.FromSeconds(0.5)], measurement.RrIntervals);
  }

  [Fact]
  public void Rejects_truncated_measurement()
  {
    Assert.Throws<FormatException>(() => HeartRateMeasurementParser.Parse([0x01, 0x48]));
    Assert.Throws<FormatException>(() => HeartRateMeasurementParser.Parse([0x08, 0x48, 0x01]));
    Assert.Throws<FormatException>(() => HeartRateMeasurementParser.Parse([0x10, 0x48, 0x01]));
  }
}
