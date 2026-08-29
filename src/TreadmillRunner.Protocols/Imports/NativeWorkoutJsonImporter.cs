using System.Text.Json;
using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.Protocols.Imports;

public sealed class NativeWorkoutJsonImporter : IWorkoutImporter
{
  private const string SupportedSchema = "treadmillrunner.workout/v1";

  public WorkoutImportFormat Format => WorkoutImportFormat.NativeJson;

  public async ValueTask<WorkoutImportResult> ImportAsync(
      Stream source,
      string fileName,
      CancellationToken cancellationToken = default)
  {
    byte[] bytes = await WorkoutImportGuard.ReadBoundedAsync(source, cancellationToken);
    try
    {
      using JsonDocument document = JsonDocument.Parse(bytes, new JsonDocumentOptions
      {
        AllowTrailingCommas = false,
        CommentHandling = JsonCommentHandling.Disallow,
        MaxDepth = 32,
      });

      JsonElement root = document.RootElement;
      RequireKind(root, JsonValueKind.Object, "The native workout root must be an object.");
      List<WorkoutImportWarning> warnings = [];
      if (!root.TryGetProperty("schema", out JsonElement schemaElement))
      {
        return ParseCanonical(root, warnings);
      }

      if (schemaElement.ValueKind != JsonValueKind.String)
      {
        throw new WorkoutImportException("Native workout field 'schema' must be a string.");
      }

      string schema = schemaElement.GetString()!;
      if (!string.Equals(schema, SupportedSchema, StringComparison.Ordinal))
      {
        throw new WorkoutImportException($"Unsupported native workout schema '{schema}'.");
      }

      string name = GetRequiredString(root, "name");
      string? description = GetOptionalString(root, "description");
      JsonElement blocksElement = GetRequired(root, "blocks");
      RequireKind(blocksElement, JsonValueKind.Array, "Native workout blocks must be an array.");

      WarnUnknownProperties(root, ["schema", "name", "description", "blocks"], "root", warnings);
      IReadOnlyList<WorkoutBlock> blocks = ParseBlocks(blocksElement, depth: 0, warnings);
      return WorkoutImportGuard.Validate(
          Format,
          new WorkoutDefinition(1, name, description, blocks),
          warnings);
    }
    catch (JsonException exception)
    {
      throw new WorkoutImportException("The native workout JSON is malformed.", exception);
    }
    catch (ArgumentException exception)
    {
      throw new WorkoutImportException("The native workout contains invalid or out-of-range values.", exception);
    }
    catch (OverflowException exception)
    {
      throw new WorkoutImportException("The native workout contains a value that is too large.", exception);
    }
  }

  private static WorkoutImportResult ParseCanonical(
      JsonElement root,
      List<WorkoutImportWarning> warnings)
  {
    int schemaVersion = GetRequiredInt(root, "schemaVersion");
    if (schemaVersion != 1)
    {
      throw new WorkoutImportException($"Unsupported native workout schema version '{schemaVersion}'.");
    }

    string title = GetRequiredString(root, "title");
    string? description = GetOptionalString(root, "description");
    JsonElement blocksElement = GetRequired(root, "blocks");
    RequireKind(blocksElement, JsonValueKind.Array, "Native workout blocks must be an array.");
    WarnUnknownProperties(root, ["schemaVersion", "title", "description", "blocks"], "root", warnings);
    IReadOnlyList<WorkoutBlock> blocks = ParseCanonicalBlocks(blocksElement, 0, warnings);
    return WorkoutImportGuard.Validate(
        WorkoutImportFormat.NativeJson,
        new WorkoutDefinition(1, title, description, blocks),
        warnings);
  }

  private static IReadOnlyList<WorkoutBlock> ParseCanonicalBlocks(
      JsonElement array,
      int depth,
      List<WorkoutImportWarning> warnings)
  {
    if (depth >= WorkoutDefinitionLimits.MaximumNestingDepth)
    {
      throw new WorkoutImportException($"Native workout repeat nesting exceeds {WorkoutDefinitionLimits.MaximumNestingDepth} levels.");
    }

    List<WorkoutBlock> blocks = [];
    foreach (JsonElement element in array.EnumerateArray())
    {
      RequireKind(element, JsonValueKind.Object, "Every native workout block must be an object.");
      string kind = GetRequiredString(element, "kind");
      if (kind == "repeat")
      {
        JsonElement children = GetRequired(element, "blocks");
        RequireKind(children, JsonValueKind.Array, "Repeat blocks must be an array.");
        WarnUnknownProperties(element, ["kind", "repetitions", "blocks"], "repeat", warnings);
        blocks.Add(new WorkoutRepeat(
            GetRequiredInt(element, "repetitions"),
            ParseCanonicalBlocks(children, depth + 1, warnings)));
        continue;
      }

      if (kind != "step")
      {
        throw new WorkoutImportException($"Unsupported native workout block kind '{kind}'.");
      }

      WarnUnknownProperties(element, ["kind", "goal", "speed", "incline", "cue", "notes"], "step", warnings);
      blocks.Add(new WorkoutStep(
          ParseCanonicalGoal(GetRequired(element, "goal"), warnings),
          ParseCanonicalSpeed(GetRequired(element, "speed"), warnings),
          ParseCanonicalIncline(GetRequired(element, "incline"), warnings),
          GetOptionalString(element, "cue"),
          GetOptionalString(element, "notes")));
    }

    return blocks;
  }

  private static StepGoal ParseCanonicalGoal(
      JsonElement element,
      List<WorkoutImportWarning> warnings)
  {
    RequireKind(element, JsonValueKind.Object, "Native workout goal must be an object.");
    string kind = GetRequiredString(element, "kind");
    IReadOnlyCollection<string> known = kind switch
    {
      "time" => ["kind", "durationTicks"],
      "distance" => ["kind", "kilometers"],
      _ => throw new WorkoutImportException($"Unsupported native workout goal kind '{kind}'."),
    };
    WarnUnknownProperties(element, known, "goal", warnings);
    return kind == "time"
      ? new TimeGoal(TimeSpan.FromTicks(GetRequiredLong(element, "durationTicks")))
      : new DistanceGoal(GetRequiredDouble(element, "kilometers"));
  }

  private static SpeedDirective ParseCanonicalSpeed(
      JsonElement element,
      List<WorkoutImportWarning> warnings)
  {
    RequireKind(element, JsonValueKind.Object, "Native workout speed must be an object.");
    string kind = GetRequiredString(element, "kind");
    IReadOnlyCollection<string> known = kind switch
    {
      "open" => ["kind"],
      "fixed" => ["kind", "kilometersPerHour"],
      "ramp" => ["kind", "startKilometersPerHour", "endKilometersPerHour"],
      "heartRate" => ["kind", "minimumBpm", "maximumBpm", "initialKilometersPerHour", "minimumKilometersPerHour", "maximumKilometersPerHour"],
      "heartRateZone" => ["kind", "zoneNumber", "initialKilometersPerHour", "minimumKilometersPerHour", "maximumKilometersPerHour"],
      _ => throw new WorkoutImportException($"Unsupported native workout speed kind '{kind}'."),
    };
    WarnUnknownProperties(element, known, "speed", warnings);
    return kind switch
    {
      "open" => new OpenSpeed(),
      "fixed" => new FixedSpeed(GetRequiredDouble(element, "kilometersPerHour")),
      "ramp" => new SpeedRamp(
          GetRequiredDouble(element, "startKilometersPerHour"),
          GetRequiredDouble(element, "endKilometersPerHour")),
      "heartRate" => new HeartRateSpeed(
          checked((ushort)GetRequiredInt(element, "minimumBpm")),
          checked((ushort)GetRequiredInt(element, "maximumBpm")),
          GetRequiredDouble(element, "initialKilometersPerHour"),
          GetRequiredDouble(element, "minimumKilometersPerHour"),
          GetRequiredDouble(element, "maximumKilometersPerHour")),
      "heartRateZone" => new HeartRateZoneSpeed(
          GetRequiredInt(element, "zoneNumber"),
          GetRequiredDouble(element, "initialKilometersPerHour"),
          GetRequiredDouble(element, "minimumKilometersPerHour"),
          GetRequiredDouble(element, "maximumKilometersPerHour")),
      _ => throw new WorkoutImportException($"Unsupported native workout speed kind '{kind}'."),
    };
  }

  private static InclineDirective ParseCanonicalIncline(
      JsonElement element,
      List<WorkoutImportWarning> warnings)
  {
    RequireKind(element, JsonValueKind.Object, "Native workout incline must be an object.");
    string kind = GetRequiredString(element, "kind");
    IReadOnlyCollection<string> known = kind switch
    {
      "fixed" => ["kind", "percent"],
      "ramp" => ["kind", "startPercent", "endPercent"],
      _ => throw new WorkoutImportException($"Unsupported native workout incline kind '{kind}'."),
    };
    WarnUnknownProperties(element, known, "incline", warnings);
    return kind switch
    {
      "fixed" => new FixedIncline(GetRequiredDouble(element, "percent")),
      "ramp" => new InclineRamp(
          GetRequiredDouble(element, "startPercent"),
          GetRequiredDouble(element, "endPercent")),
      _ => throw new WorkoutImportException($"Unsupported native workout incline kind '{kind}'."),
    };
  }

  private static IReadOnlyList<WorkoutBlock> ParseBlocks(
      JsonElement array,
      int depth,
      List<WorkoutImportWarning> warnings)
  {
    if (depth >= 8)
    {
      throw new WorkoutImportException("Native workout repeat nesting exceeds eight levels.");
    }

    List<WorkoutBlock> blocks = [];
    foreach (JsonElement element in array.EnumerateArray())
    {
      RequireKind(element, JsonValueKind.Object, "Every native workout block must be an object.");
      string type = GetRequiredString(element, "type");
      switch (type)
      {
        case "step":
          WarnUnknownProperties(
              element,
              [
                "type", "name", "durationSeconds", "distanceMeters", "speedStartKph", "speedEndKph",
                "inclineStartPercent", "inclineEndPercent", "heartRateZone", "heartRateMinBpm",
                "heartRateMaxBpm", "minimumSpeedKph", "maximumSpeedKph", "cue", "notes",
              ],
              "step",
              warnings);
          blocks.Add(ParseStep(element, warnings));
          break;

        case "repeat":
          WarnUnknownProperties(element, ["type", "count", "blocks"], "repeat", warnings);
          int count = GetRequiredInt(element, "count");
          JsonElement nestedBlocks = GetRequired(element, "blocks");
          RequireKind(nestedBlocks, JsonValueKind.Array, "Repeat blocks must be an array.");
          blocks.Add(new WorkoutRepeat(count, ParseBlocks(nestedBlocks, depth + 1, warnings)));
          break;

        default:
          throw new WorkoutImportException($"Unsupported native workout block type '{type}'.");
      }
    }

    return blocks;
  }

  private static WorkoutStep ParseStep(JsonElement element, List<WorkoutImportWarning> warnings)
  {
    TimeSpan? duration = GetOptionalTimeSpan(element, "durationSeconds");
    double? distanceMeters = GetOptionalDouble(element, "distanceMeters");
    if ((duration is null) == (distanceMeters is null))
    {
      throw new WorkoutImportException("Each native workout step must define exactly one durationSeconds or distanceMeters value.");
    }

    StepGoal goal = duration is not null
        ? new TimeGoal(duration.Value)
        : new DistanceGoal(distanceMeters!.Value / 1000);

    double? speedStart = GetOptionalDouble(element, "speedStartKph");
    double? speedEnd = GetOptionalDouble(element, "speedEndKph");
    int? zone = GetOptionalInt(element, "heartRateZone");
    int? minimumBpm = GetOptionalInt(element, "heartRateMinBpm");
    int? maximumBpm = GetOptionalInt(element, "heartRateMaxBpm");
    double? minimumSpeed = GetOptionalDouble(element, "minimumSpeedKph");
    double? maximumSpeed = GetOptionalDouble(element, "maximumSpeedKph");

    SpeedDirective speed;
    if (zone is not null)
    {
      (double initial, double minimum, double maximum) = RequireHeartRateSpeeds(
          speedStart,
          minimumSpeed,
          maximumSpeed,
          "heart-rate zone");
      speed = new HeartRateZoneSpeed(zone.Value, initial, minimum, maximum);
    }
    else if (minimumBpm is not null || maximumBpm is not null)
    {
      if (minimumBpm is not > 0 || maximumBpm is not > 0)
      {
        throw new WorkoutImportException("Native explicit heart-rate targets require both heartRateMinBpm and heartRateMaxBpm.");
      }

      (double initial, double minimum, double maximum) = RequireHeartRateSpeeds(
          speedStart,
          minimumSpeed,
          maximumSpeed,
          "explicit heart-rate");
      speed = new HeartRateSpeed(
          checked((ushort)minimumBpm.Value),
          checked((ushort)maximumBpm.Value),
          initial,
          minimum,
          maximum);
    }
    else if (speedEnd is not null)
    {
      if (speedStart is null)
      {
        throw new WorkoutImportException("Native speed ramps require speedStartKph.");
      }

      speed = new SpeedRamp(speedStart.Value, speedEnd.Value);
    }
    else if (speedStart is not null)
    {
      speed = new FixedSpeed(speedStart.Value);
    }
    else
    {
      speed = new OpenSpeed();
      warnings.Add(new WorkoutImportWarning(
          "native.open-speed",
          "A native workout step has no speed target and requires review before execution."));
    }

    double? inclineStart = GetOptionalDouble(element, "inclineStartPercent");
    double? inclineEnd = GetOptionalDouble(element, "inclineEndPercent");
    InclineDirective incline = inclineEnd is not null
        ? new InclineRamp(
            inclineStart ?? throw new WorkoutImportException("Native incline ramps require inclineStartPercent."),
            inclineEnd.Value)
        : new FixedIncline(inclineStart ?? 0);

    string? cue = GetOptionalString(element, "cue") ?? GetOptionalString(element, "name");
    return new WorkoutStep(goal, speed, incline, cue, GetOptionalString(element, "notes"));
  }

  private static (double Initial, double Minimum, double Maximum) RequireHeartRateSpeeds(
      double? initial,
      double? minimum,
      double? maximum,
      string targetName)
  {
    if (minimum is null || maximum is null)
    {
      throw new WorkoutImportException($"A native {targetName} target requires minimumSpeedKph and maximumSpeedKph.");
    }

    return (initial ?? minimum.Value, minimum.Value, maximum.Value);
  }

  private static void WarnUnknownProperties(
      JsonElement element,
      IReadOnlyCollection<string> known,
      string context,
      List<WorkoutImportWarning> warnings)
  {
    foreach (JsonProperty property in element.EnumerateObject())
    {
      if (!known.Contains(property.Name, StringComparer.Ordinal))
      {
        warnings.Add(new WorkoutImportWarning(
            "native.unknown-field",
            $"The {context} field '{property.Name}' is not supported and was ignored."));
      }
    }
  }

  private static JsonElement GetRequired(JsonElement element, string propertyName)
  {
    if (!element.TryGetProperty(propertyName, out JsonElement value))
    {
      throw new WorkoutImportException($"Native workout field '{propertyName}' is required.");
    }

    return value;
  }

  private static string GetRequiredString(JsonElement element, string propertyName)
  {
    JsonElement value = GetRequired(element, propertyName);
    if (value.ValueKind != JsonValueKind.String || string.IsNullOrWhiteSpace(value.GetString()))
    {
      throw new WorkoutImportException($"Native workout field '{propertyName}' must be a non-empty string.");
    }

    return value.GetString()!;
  }

  private static string? GetOptionalString(JsonElement element, string propertyName)
  {
    if (!element.TryGetProperty(propertyName, out JsonElement value) || value.ValueKind == JsonValueKind.Null)
    {
      return null;
    }

    if (value.ValueKind != JsonValueKind.String)
    {
      throw new WorkoutImportException($"Native workout field '{propertyName}' must be a string.");
    }

    return value.GetString();
  }

  private static int GetRequiredInt(JsonElement element, string propertyName)
  {
    JsonElement value = GetRequired(element, propertyName);
    if (!value.TryGetInt32(out int result))
    {
      throw new WorkoutImportException($"Native workout field '{propertyName}' must be an integer.");
    }

    return result;
  }

  private static long GetRequiredLong(JsonElement element, string propertyName)
  {
    JsonElement value = GetRequired(element, propertyName);
    if (!value.TryGetInt64(out long result))
    {
      throw new WorkoutImportException($"Native workout field '{propertyName}' must be an integer.");
    }

    return result;
  }

  private static double GetRequiredDouble(JsonElement element, string propertyName)
  {
    return GetOptionalDouble(element, propertyName)
        ?? throw new WorkoutImportException($"Native workout field '{propertyName}' is required.");
  }

  private static int? GetOptionalInt(JsonElement element, string propertyName)
  {
    if (!element.TryGetProperty(propertyName, out JsonElement value) || value.ValueKind == JsonValueKind.Null)
    {
      return null;
    }

    if (!value.TryGetInt32(out int result))
    {
      throw new WorkoutImportException($"Native workout field '{propertyName}' must be an integer.");
    }

    return result;
  }

  private static double? GetOptionalDouble(JsonElement element, string propertyName)
  {
    if (!element.TryGetProperty(propertyName, out JsonElement value) || value.ValueKind == JsonValueKind.Null)
    {
      return null;
    }

    if (!value.TryGetDouble(out double result) || !double.IsFinite(result))
    {
      throw new WorkoutImportException($"Native workout field '{propertyName}' must be a finite number.");
    }

    return result;
  }

  private static TimeSpan? GetOptionalTimeSpan(JsonElement element, string propertyName)
  {
    double? seconds = GetOptionalDouble(element, propertyName);
    if (seconds is null)
    {
      return null;
    }

    try
    {
      return TimeSpan.FromSeconds(seconds.Value);
    }
    catch (OverflowException exception)
    {
      throw new WorkoutImportException($"Native workout field '{propertyName}' is too large.", exception);
    }
  }

  private static void RequireKind(JsonElement value, JsonValueKind kind, string message)
  {
    if (value.ValueKind != kind)
    {
      throw new WorkoutImportException(message);
    }
  }
}
