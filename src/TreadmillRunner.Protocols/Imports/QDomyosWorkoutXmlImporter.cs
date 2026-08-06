using System.Globalization;
using System.Xml;
using System.Xml.Linq;
using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.Protocols.Imports;

public sealed class QDomyosWorkoutXmlImporter : IWorkoutImporter
{
  private static readonly HashSet<string> SupportedRowAttributes = new(StringComparer.Ordinal)
  {
    "duration", "distance", "speed", "speedfrom", "speedto", "inclination", "zonehr", "hrmin", "hrmax",
    "minspeed", "maxspeed", "forcespeed", "looptimehr",
  };

  private static readonly HashSet<string> BikeOnlyRowAttributes = new(StringComparer.Ordinal)
  {
    "resistance", "lower_resistance", "upper_resistance", "maxresistance", "cadence", "lower_cadence",
    "upper_cadence", "power", "powerzone", "powerzonefrom", "powerzoneto", "fanspeed",
    "requested_peloton_resistance", "lower_requested_peloton_resistance", "upper_requested_peloton_resistance",
  };

  public WorkoutImportFormat Format => WorkoutImportFormat.QDomyosXml;

  public async ValueTask<WorkoutImportResult> ImportAsync(
      Stream source,
      string fileName,
      CancellationToken cancellationToken = default) =>
    await ImportAsync(source, fileName, preferBoundedHeartRate: false, cancellationToken);

  internal async ValueTask<WorkoutImportResult> ImportBundleV4Async(
      Stream source,
      string fileName,
      CancellationToken cancellationToken = default) =>
    await ImportAsync(source, fileName, preferBoundedHeartRate: true, cancellationToken);

  private async ValueTask<WorkoutImportResult> ImportAsync(
      Stream source,
      string fileName,
      bool preferBoundedHeartRate,
      CancellationToken cancellationToken)
  {
    byte[] bytes = await WorkoutImportGuard.ReadBoundedAsync(source, cancellationToken);
    try
    {
      using MemoryStream input = new(bytes, writable: false);
      using XmlReader reader = XmlReader.Create(input, new XmlReaderSettings
      {
        Async = false,
        DtdProcessing = DtdProcessing.Prohibit,
        XmlResolver = null,
        IgnoreComments = true,
        IgnoreProcessingInstructions = true,
        MaxCharactersFromEntities = 0,
        MaxCharactersInDocument = WorkoutImportLimits.MaximumBytes,
      });
      XDocument document = XDocument.Load(reader, LoadOptions.None);
      XElement root = document.Root
          ?? throw new WorkoutImportException("The QDomyos XML document has no root element.");
      if (root.Name != "rows")
      {
        throw new WorkoutImportException("The QDomyos XML root must be <rows>.");
      }

      List<WorkoutImportWarning> warnings = [];
      string? device = AttributeValue(root, "device");
      if (device is not null &&
          !string.Equals(device, "treadmill", StringComparison.OrdinalIgnoreCase) &&
          !string.Equals(device, "unknown", StringComparison.OrdinalIgnoreCase))
      {
        warnings.Add(new WorkoutImportWarning(
            "qdomyos.non-treadmill-device",
            $"The source declares device '{device}'. Only treadmill fields are imported."));
      }

      foreach (XAttribute attribute in root.Attributes())
      {
        if (attribute.Name != "device")
        {
          warnings.Add(new WorkoutImportWarning(
              "qdomyos.unknown-root-field",
              $"The root attribute '{attribute.Name.LocalName}' is not supported and was ignored."));
        }
      }

      bool usedSpeedUnit = false;
      IReadOnlyList<WorkoutBlock> blocks = ParseBlocks(
        root.Elements(), depth: 0, warnings, preferBoundedHeartRate, ref usedSpeedUnit);
      if (usedSpeedUnit)
      {
        warnings.Add(new WorkoutImportWarning(
            "qdomyos.assumed-speed-units",
            "QDomyos XML does not reliably declare speed units; speed values were assumed to be km/h."));
      }

      string name = Path.GetFileNameWithoutExtension(fileName);
      if (string.IsNullOrWhiteSpace(name))
      {
        name = "Imported QDomyos workout";
      }

      return WorkoutImportGuard.Validate(
          Format,
          new WorkoutDefinition(1, name, null, blocks),
          warnings);
    }
    catch (XmlException exception)
    {
      throw new WorkoutImportException("The QDomyos XML is malformed or contains a prohibited DTD/entity.", exception);
    }
    catch (ArgumentException exception)
    {
      throw new WorkoutImportException("The QDomyos workout contains invalid or out-of-range values.", exception);
    }
    catch (OverflowException exception)
    {
      throw new WorkoutImportException("The QDomyos workout contains a value that is too large.", exception);
    }
  }

  private static IReadOnlyList<WorkoutBlock> ParseBlocks(
      IEnumerable<XElement> elements,
      int depth,
      List<WorkoutImportWarning> warnings,
      bool preferBoundedHeartRate,
      ref bool usedSpeedUnit)
  {
    if (depth >= 8)
    {
      throw new WorkoutImportException("QDomyos repeat nesting exceeds eight levels.");
    }

    List<WorkoutBlock> blocks = [];
    foreach (XElement element in elements)
    {
      if (element.Name == "repeat")
      {
        int count = RequiredIntAttribute(element, "times");
        foreach (XAttribute attribute in element.Attributes())
        {
          if (attribute.Name != "times")
          {
            warnings.Add(new WorkoutImportWarning(
                "qdomyos.unknown-repeat-field",
                $"The repeat attribute '{attribute.Name.LocalName}' is not supported and was ignored."));
          }
        }

        IReadOnlyList<WorkoutBlock> nested = ParseBlocks(
            element.Elements(),
            depth + 1,
            warnings,
            preferBoundedHeartRate,
            ref usedSpeedUnit);
        blocks.Add(new WorkoutRepeat(count, nested));
        continue;
      }

      if (element.Name != "row")
      {
        warnings.Add(new WorkoutImportWarning(
            "qdomyos.unknown-element",
            $"The element <{element.Name.LocalName}> is not supported and was ignored."));
        continue;
      }

      blocks.Add(ParseStep(element, warnings, preferBoundedHeartRate, ref usedSpeedUnit));
    }

    return blocks;
  }

  private static WorkoutStep ParseStep(
      XElement row,
      List<WorkoutImportWarning> warnings,
      bool preferBoundedHeartRate,
      ref bool usedSpeedUnit)
  {
    foreach (XAttribute attribute in row.Attributes())
    {
      string name = attribute.Name.LocalName;
      if (BikeOnlyRowAttributes.Contains(name))
      {
        warnings.Add(new WorkoutImportWarning(
            "qdomyos.unsupported-bike-field",
            $"The bike-oriented row field '{name}' is not supported and was ignored."));
      }
      else if (!SupportedRowAttributes.Contains(name))
      {
        warnings.Add(new WorkoutImportWarning(
            "qdomyos.unknown-row-field",
            $"The row field '{name}' is not supported and was ignored."));
      }
    }

    TimeSpan? duration = ParseOptionalDuration(AttributeValue(row, "duration"));
    double? distanceKilometers = OptionalDoubleAttribute(row, "distance");
    double? speed = OptionalDoubleAttribute(row, "speed");
    double? speedFrom = OptionalDoubleAttribute(row, "speedfrom");
    double? speedTo = OptionalDoubleAttribute(row, "speedto");
    double? minimumSpeed = OptionalDoubleAttribute(row, "minspeed");
    double? maximumSpeed = OptionalDoubleAttribute(row, "maxspeed");

    if (speed is not null || speedFrom is not null || speedTo is not null ||
        minimumSpeed is not null || maximumSpeed is not null)
    {
      usedSpeedUnit = true;
    }

    if ((speedFrom is null) != (speedTo is null))
    {
      throw new WorkoutImportException("QDomyos speed ramps require both speedfrom and speedto.");
    }

    if (speed is not null && speedFrom is not null)
    {
      throw new WorkoutImportException("A QDomyos row cannot define both fixed speed and a speed ramp.");
    }

    int? heartRateZone = OptionalIntAttribute(row, "zonehr");
    if (heartRateZone == 0)
    {
      heartRateZone = null;
    }

    int? heartRateMin = OptionalIntAttribute(row, "hrmin");
    int? heartRateMax = OptionalIntAttribute(row, "hrmax");
    if (heartRateZone is not null && (heartRateMin is not null || heartRateMax is not null))
    {
      warnings.Add(new WorkoutImportWarning(
          "qdomyos.conflicting-heart-rate-targets",
          "The row defines both a heart-rate zone and explicit heart-rate bounds; the explicit bounds were retained and the zone was ignored."));
    }

    if (AttributeValue(row, "forcespeed") is { } forceSpeed && forceSpeed != "0")
    {
      warnings.Add(new WorkoutImportWarning(
          "qdomyos.forcespeed-ignored",
          "The QDomyos forcespeed flag was recorded only as import context and does not enable any treadmill capability."));
    }

    if (AttributeValue(row, "looptimehr") is not null)
    {
      warnings.Add(new WorkoutImportWarning(
          "qdomyos.hr-loop-ignored",
          "The QDomyos HR loop interval was ignored; TreadmillRunner uses its own recorded safety controller settings."));
    }

    List<string> cues = [];
    foreach (XElement child in row.Elements())
    {
      if (child.Name == "textevent")
      {
        string? message = AttributeValue(child, "message");
        if (!string.IsNullOrWhiteSpace(message))
        {
          cues.Add(message);
        }

        if (AttributeValue(child, "timeoffset") is not null)
        {
          warnings.Add(new WorkoutImportWarning(
              "qdomyos.text-offset-flattened",
              "A QDomyos text-event offset was flattened into the containing step cue."));
        }

        continue;
      }

      warnings.Add(new WorkoutImportWarning(
          "qdomyos.unknown-row-element",
          $"The row element <{child.Name.LocalName}> is not supported and was ignored."));
    }

    if ((duration is null) == (distanceKilometers is null))
    {
      throw new WorkoutImportException("Each QDomyos row must define exactly one duration or distance.");
    }

    StepGoal goal = duration is not null
        ? new TimeGoal(duration.Value)
        : new DistanceGoal(distanceKilometers!.Value);

    bool hasFixedOrRampSpeed = speed is not null || speedFrom is not null;
    bool hasHeartRateTarget = heartRateZone is not null || heartRateMin is not null || heartRateMax is not null;
    bool useBoundedHeartRate = preferBoundedHeartRate && speed is > 0 && speedFrom is null &&
      minimumSpeed is > 0 && maximumSpeed is > 0 && hasHeartRateTarget;
    if (hasFixedOrRampSpeed && hasHeartRateTarget && !useBoundedHeartRate)
    {
      warnings.Add(new WorkoutImportWarning(
          "qdomyos.conflicting-speed-target",
          "The row combines fixed/ramp speed with heart-rate control. The explicit speed was retained and HR automation was not enabled."));
    }

    SpeedDirective speedDirective;
    if (useBoundedHeartRate && heartRateMin is not null && heartRateMax is not null)
    {
      speedDirective = new HeartRateSpeed(
        checked((ushort)heartRateMin.Value),
        checked((ushort)heartRateMax.Value),
        speed!.Value,
        minimumSpeed!.Value,
        maximumSpeed!.Value);
      warnings.Add(new WorkoutImportWarning(
        "qdomyos.v4-bounded-heart-rate",
        "The v4 bundle's explicit heart-rate target and speed bounds were retained as an adaptive directive."));
    }
    else if (useBoundedHeartRate && heartRateZone is not null)
    {
      speedDirective = new HeartRateZoneSpeed(
        heartRateZone.Value,
        speed!.Value,
        minimumSpeed!.Value,
        maximumSpeed!.Value);
      warnings.Add(new WorkoutImportWarning(
        "qdomyos.v4-bounded-heart-rate-zone",
        "The v4 bundle's heart-rate zone and speed bounds were retained as an adaptive directive."));
    }
    else if (speedTo is not null)
    {
      speedDirective = new SpeedRamp(speedFrom!.Value, speedTo.Value);
    }
    else if (speed is not null)
    {
      speedDirective = new FixedSpeed(speed.Value);
    }
    else if (heartRateMin is not null || heartRateMax is not null)
    {
      if (heartRateMin is not > 0 || heartRateMax is not > 0)
      {
        throw new WorkoutImportException("QDomyos explicit HR targets require both hrmin and hrmax.");
      }

      (double initial, double minimum, double maximum) = HeartRateSpeeds(
          minimumSpeed,
          maximumSpeed,
          warnings);
      speedDirective = new HeartRateSpeed(
          checked((ushort)heartRateMin.Value),
          checked((ushort)heartRateMax.Value),
          initial,
          minimum,
          maximum);
    }
    else if (heartRateZone is not null)
    {
      (double initial, double minimum, double maximum) = HeartRateSpeeds(
          minimumSpeed,
          maximumSpeed,
          warnings);
      speedDirective = new HeartRateZoneSpeed(heartRateZone.Value, initial, minimum, maximum);
    }
    else
    {
      speedDirective = new OpenSpeed();
      warnings.Add(new WorkoutImportWarning(
          "qdomyos.open-speed",
          "A QDomyos row has no treadmill speed target and requires review before execution."));
    }

    InclineDirective incline = new FixedIncline(OptionalDoubleAttribute(row, "inclination") ?? 0);
    return new WorkoutStep(
        goal,
        speedDirective,
        incline,
        cues.Count == 0 ? null : string.Join(" · ", cues));
  }

  private static (double Initial, double Minimum, double Maximum) HeartRateSpeeds(
      double? minimumSpeed,
      double? maximumSpeed,
      List<WorkoutImportWarning> warnings)
  {
    double minimum = minimumSpeed ?? 0;
    double maximum = maximumSpeed ?? minimum;
    if (minimumSpeed is null || maximumSpeed is null)
    {
      warnings.Add(new WorkoutImportWarning(
          "qdomyos.hr-speed-bounds-defaulted",
          "The source omitted one or both HR speed bounds; missing values were conservatively set to zero/minimum and require review."));
    }

    warnings.Add(new WorkoutImportWarning(
        "qdomyos.hr-initial-speed-defaulted",
        "QDomyos HR rows do not declare an initial speed; the minimum speed was used and requires review."));
    return (minimum, minimum, maximum);
  }

  private static string? AttributeValue(XElement element, XName name) =>
      element.Attribute(name)?.Value;

  private static int RequiredIntAttribute(XElement element, XName name)
  {
    string? text = AttributeValue(element, name);
    if (!int.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out int value))
    {
      throw new WorkoutImportException($"QDomyos attribute '{name}' must be an integer.");
    }

    return value;
  }

  private static int? OptionalIntAttribute(XElement element, XName name)
  {
    string? text = AttributeValue(element, name);
    if (text is null)
    {
      return null;
    }

    if (!int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out int value))
    {
      throw new WorkoutImportException($"QDomyos attribute '{name}' must be an integer.");
    }

    return value;
  }

  private static double? OptionalDoubleAttribute(XElement element, XName name)
  {
    string? text = AttributeValue(element, name);
    if (text is null)
    {
      return null;
    }

    if (!double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out double value) ||
        !double.IsFinite(value))
    {
      throw new WorkoutImportException($"QDomyos attribute '{name}' must be a finite number.");
    }

    return value;
  }

  private static TimeSpan? ParseOptionalDuration(string? text)
  {
    if (text is null)
    {
      return null;
    }

    string[] parts = text.Split(':');
    if (parts.Length != 3 ||
        !int.TryParse(parts[0], NumberStyles.None, CultureInfo.InvariantCulture, out int hours) ||
        !int.TryParse(parts[1], NumberStyles.None, CultureInfo.InvariantCulture, out int minutes) ||
        !int.TryParse(parts[2], NumberStyles.None, CultureInfo.InvariantCulture, out int seconds) ||
        hours < 0 || minutes is < 0 or > 59 || seconds is < 0 or > 59)
    {
      throw new WorkoutImportException("QDomyos duration values must use HH:MM:SS.");
    }

    try
    {
      return new TimeSpan(hours, minutes, seconds);
    }
    catch (ArgumentOutOfRangeException exception)
    {
      throw new WorkoutImportException("The QDomyos duration is too large.", exception);
    }
  }
}
