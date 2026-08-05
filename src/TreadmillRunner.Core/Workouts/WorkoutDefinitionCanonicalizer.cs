using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace TreadmillRunner.Core.Workouts;

public static class WorkoutDefinitionCanonicalizer
{
  public static string Serialize(WorkoutDefinition definition)
  {
    ArgumentNullException.ThrowIfNull(definition);

    using var stream = new MemoryStream();
    using (var writer = new Utf8JsonWriter(stream, new JsonWriterOptions { Indented = false }))
    {
      writer.WriteStartObject();
      writer.WriteNumber("schemaVersion", definition.SchemaVersion);
      writer.WriteString("title", definition.Title);
      if (definition.Description is null)
      {
        writer.WriteNull("description");
      }
      else
      {
        writer.WriteString("description", definition.Description);
      }

      writer.WriteStartArray("blocks");
      foreach (var block in definition.Blocks)
      {
        WriteBlock(writer, block);
      }

      writer.WriteEndArray();
      writer.WriteEndObject();
    }

    return Encoding.UTF8.GetString(stream.ToArray());
  }

  public static string ComputeSha256(WorkoutDefinition definition)
  {
    var bytes = Encoding.UTF8.GetBytes(Serialize(definition));
    return Convert.ToHexStringLower(SHA256.HashData(bytes));
  }

  private static void WriteBlock(Utf8JsonWriter writer, WorkoutBlock block)
  {
    writer.WriteStartObject();
    switch (block)
    {
      case WorkoutStep step:
        writer.WriteString("kind", "step");
        WriteGoal(writer, step.Goal);
        WriteSpeed(writer, step.Speed);
        WriteIncline(writer, step.Incline);
        WriteOptionalString(writer, "cue", step.Cue);
        WriteOptionalString(writer, "notes", step.Notes);
        break;
      case WorkoutRepeat repeat:
        writer.WriteString("kind", "repeat");
        writer.WriteNumber("repetitions", repeat.Repetitions);
        writer.WriteStartArray("blocks");
        foreach (var child in repeat.Blocks)
        {
          WriteBlock(writer, child);
        }

        writer.WriteEndArray();
        break;
      default:
        throw new InvalidOperationException($"Unsupported workout block type '{block.GetType().Name}'.");
    }

    writer.WriteEndObject();
  }

  private static void WriteGoal(Utf8JsonWriter writer, StepGoal goal)
  {
    writer.WriteStartObject("goal");
    switch (goal)
    {
      case TimeGoal time:
        writer.WriteString("kind", "time");
        writer.WriteNumber("durationTicks", time.Duration.Ticks);
        break;
      case DistanceGoal distance:
        writer.WriteString("kind", "distance");
        writer.WriteNumber("kilometers", distance.Kilometers);
        break;
      default:
        throw new InvalidOperationException($"Unsupported step goal type '{goal.GetType().Name}'.");
    }

    writer.WriteEndObject();
  }

  private static void WriteSpeed(Utf8JsonWriter writer, SpeedDirective speed)
  {
    writer.WriteStartObject("speed");
    switch (speed)
    {
      case OpenSpeed:
        writer.WriteString("kind", "open");
        break;
      case FixedSpeed fixedSpeed:
        writer.WriteString("kind", "fixed");
        writer.WriteNumber("kilometersPerHour", fixedSpeed.KilometersPerHour);
        break;
      case SpeedRamp ramp:
        writer.WriteString("kind", "ramp");
        writer.WriteNumber("startKilometersPerHour", ramp.StartKilometersPerHour);
        writer.WriteNumber("endKilometersPerHour", ramp.EndKilometersPerHour);
        break;
      case HeartRateSpeed heartRate:
        writer.WriteString("kind", "heartRate");
        writer.WriteNumber("minimumBpm", heartRate.MinimumBpm);
        writer.WriteNumber("maximumBpm", heartRate.MaximumBpm);
        writer.WriteNumber("initialKilometersPerHour", heartRate.InitialKilometersPerHour);
        writer.WriteNumber("minimumKilometersPerHour", heartRate.MinimumKilometersPerHour);
        writer.WriteNumber("maximumKilometersPerHour", heartRate.MaximumKilometersPerHour);
        break;
      case HeartRateZoneSpeed zone:
        writer.WriteString("kind", "heartRateZone");
        writer.WriteNumber("zoneNumber", zone.ZoneNumber);
        writer.WriteNumber("initialKilometersPerHour", zone.InitialKilometersPerHour);
        writer.WriteNumber("minimumKilometersPerHour", zone.MinimumKilometersPerHour);
        writer.WriteNumber("maximumKilometersPerHour", zone.MaximumKilometersPerHour);
        break;
      default:
        throw new InvalidOperationException($"Unsupported speed directive type '{speed.GetType().Name}'.");
    }

    writer.WriteEndObject();
  }

  private static void WriteIncline(Utf8JsonWriter writer, InclineDirective incline)
  {
    writer.WriteStartObject("incline");
    switch (incline)
    {
      case FixedIncline fixedIncline:
        writer.WriteString("kind", "fixed");
        writer.WriteNumber("percent", fixedIncline.Percent);
        break;
      case InclineRamp ramp:
        writer.WriteString("kind", "ramp");
        writer.WriteNumber("startPercent", ramp.StartPercent);
        writer.WriteNumber("endPercent", ramp.EndPercent);
        break;
      default:
        throw new InvalidOperationException($"Unsupported incline directive type '{incline.GetType().Name}'.");
    }

    writer.WriteEndObject();
  }

  private static void WriteOptionalString(Utf8JsonWriter writer, string propertyName, string? value)
  {
    if (value is null)
    {
      writer.WriteNull(propertyName);
    }
    else
    {
      writer.WriteString(propertyName, value);
    }
  }
}
