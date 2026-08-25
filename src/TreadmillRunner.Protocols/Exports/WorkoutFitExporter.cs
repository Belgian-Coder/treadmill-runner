using System.Globalization;
using System.Text.Json;
using Dynastream.Fit;

namespace TreadmillRunner.Protocols.Exports;

public static class WorkoutFitExporter
{
  public static byte[] Export(Guid revisionId, string definitionJson, DateTimeOffset createdAtUtc)
  {
    if (revisionId == Guid.Empty) throw new ArgumentException("Workout revision ID is required.", nameof(revisionId));
    ArgumentException.ThrowIfNullOrWhiteSpace(definitionJson);
    using JsonDocument document = JsonDocument.Parse(definitionJson);
    JsonElement root = document.RootElement;
    string title = root.GetProperty("title").GetString() ?? "TreadmillRunner workout";
    var steps = new List<WorkoutStepMesg>();
    AppendBlocks(root.GetProperty("blocks"), steps);
    if (steps.Count is 0 or > ushort.MaxValue)
      throw new InvalidOperationException("The workout cannot be represented within FIT workout step limits.");

    using var stream = new MemoryStream();
    var encoder = new Encode(stream, ProtocolVersion.V20);
    var fileId = new FileIdMesg();
    fileId.SetType(Dynastream.Fit.File.Workout);
    fileId.SetManufacturer((ushort)Manufacturer.Development);
    fileId.SetProduct(1);
    fileId.SetSerialNumber(BitConverter.ToUInt32(revisionId.ToByteArray(), 0));
    fileId.SetTimeCreated(new Dynastream.Fit.DateTime(createdAtUtc.UtcDateTime));
    fileId.SetProductName("TreadmillRunner");
    encoder.Write(fileId);

    var workout = new WorkoutMesg();
    workout.SetWktName(title);
    workout.SetSport(Sport.Running);
    workout.SetNumValidSteps((ushort)steps.Count);
    encoder.Write(workout);
    foreach (WorkoutStepMesg step in steps) encoder.Write(step);
    encoder.Close();
    return stream.ToArray();
  }

  private static void AppendBlocks(JsonElement blocks, List<WorkoutStepMesg> output)
  {
    foreach (JsonElement block in blocks.EnumerateArray())
    {
      string kind = block.GetProperty("kind").GetString() ?? string.Empty;
      if (kind == "step")
      {
        output.Add(CreateStep(block, checked((ushort)output.Count)));
        continue;
      }
      if (kind != "repeat") throw new InvalidOperationException($"Unsupported workout block '{kind}'.");

      int startIndex = output.Count;
      AppendBlocks(block.GetProperty("blocks"), output);
      int repetitions = block.GetProperty("repetitions").GetInt32();
      var marker = new WorkoutStepMesg();
      marker.SetMessageIndex(checked((ushort)output.Count));
      marker.SetWktStepName($"Repeat {repetitions} times");
      marker.SetDurationType(WktStepDuration.RepeatUntilStepsCmplt);
      marker.SetDurationStep(checked((uint)startIndex));
      marker.SetTargetType(WktStepTarget.Open);
      marker.SetRepeatSteps(checked((uint)repetitions));
      output.Add(marker);
    }
  }

  private static WorkoutStepMesg CreateStep(JsonElement block, ushort index)
  {
    var message = new WorkoutStepMesg();
    message.SetMessageIndex(index);
    string? cue = String(block, "cue");
    message.SetWktStepName(cue ?? $"Step {index + 1}");

    JsonElement goal = block.GetProperty("goal");
    switch (goal.GetProperty("kind").GetString())
    {
      case "time":
        message.SetDurationType(WktStepDuration.Time);
        message.SetDurationTime((float)(goal.GetProperty("durationTicks").GetInt64() / (double)TimeSpan.TicksPerSecond));
        break;
      case "distance":
        message.SetDurationType(WktStepDuration.Distance);
        message.SetDurationDistance((float)(goal.GetProperty("kilometers").GetDouble() * 1000));
        break;
      default:
        throw new InvalidOperationException("FIT export supports time and distance workout goals only.");
    }

    JsonElement speed = block.GetProperty("speed");
    string speedKind = speed.GetProperty("kind").GetString() ?? string.Empty;
    switch (speedKind)
    {
      case "open":
        message.SetTargetType(WktStepTarget.Open);
        break;
      case "fixed":
        message.SetTargetType(WktStepTarget.Speed);
        float metersPerSecond = (float)(speed.GetProperty("kilometersPerHour").GetDouble() / 3.6);
        message.SetCustomTargetSpeedLow(metersPerSecond);
        message.SetCustomTargetSpeedHigh(metersPerSecond);
        break;
      case "ramp":
        message.SetTargetType(WktStepTarget.Speed);
        float start = (float)(speed.GetProperty("startKilometersPerHour").GetDouble() / 3.6);
        float end = (float)(speed.GetProperty("endKilometersPerHour").GetDouble() / 3.6);
        message.SetCustomTargetSpeedLow(Math.Min(start, end));
        message.SetCustomTargetSpeedHigh(Math.Max(start, end));
        break;
      case "heartRate":
        message.SetTargetType(WktStepTarget.HeartRate);
        message.SetCustomTargetHeartRateLow(checked((uint)speed.GetProperty("minimumBpm").GetUInt16() + 100));
        message.SetCustomTargetHeartRateHigh(checked((uint)speed.GetProperty("maximumBpm").GetUInt16() + 100));
        break;
      case "heartRateZone":
        message.SetTargetType(WktStepTarget.HeartRate);
        message.SetTargetHrZone(checked((uint)speed.GetProperty("zoneNumber").GetInt32()));
        break;
      default:
        throw new InvalidOperationException($"FIT export does not support speed directive '{speedKind}'.");
    }

    string? originalNotes = String(block, "notes");
    string fitNotes = BuildNotes(originalNotes, speed, block.GetProperty("incline"));
    if (!string.IsNullOrWhiteSpace(fitNotes)) message.SetNotes(fitNotes);
    return message;
  }

  private static string BuildNotes(string? original, JsonElement speed, JsonElement incline)
  {
    var notes = new List<string>();
    if (!string.IsNullOrWhiteSpace(original)) notes.Add(original);
    string speedKind = speed.GetProperty("kind").GetString() ?? string.Empty;
    if (speedKind == "ramp")
      notes.Add($"Speed ramp {Number(speed.GetProperty("startKilometersPerHour").GetDouble())}-{Number(speed.GetProperty("endKilometersPerHour").GetDouble())} km/h; FIT stores the endpoints as a target range.");
    if (speedKind is "heartRate" or "heartRateZone")
      notes.Add("Treadmill speed safety bounds remain in the immutable TreadmillRunner revision and are not a standard FIT workout target.");

    string inclineKind = incline.GetProperty("kind").GetString() ?? string.Empty;
    notes.Add(inclineKind switch
    {
      "fixed" => $"Treadmill incline {Number(incline.GetProperty("percent").GetDouble())}%.",
      "ramp" => $"Treadmill incline ramp {Number(incline.GetProperty("startPercent").GetDouble())}-{Number(incline.GetProperty("endPercent").GetDouble())}%.",
      _ => "Treadmill incline is defined in the immutable TreadmillRunner revision.",
    });
    return string.Join(" ", notes);
  }

  private static string? String(JsonElement element, string name) =>
    element.TryGetProperty(name, out JsonElement value) && value.ValueKind == JsonValueKind.String
      ? value.GetString()
      : null;

  private static string Number(double value) => value.ToString("0.##", CultureInfo.InvariantCulture);
}
