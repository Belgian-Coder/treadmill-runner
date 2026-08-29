using Dynastream.Fit;
using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.Protocols.Imports;

public sealed class GarminFitWorkoutImporter : IWorkoutImporter
{
  public WorkoutImportFormat Format => WorkoutImportFormat.GarminFit;

  public async ValueTask<WorkoutImportResult> ImportAsync(
      Stream source,
      string fileName,
      CancellationToken cancellationToken = default)
  {
    byte[] bytes = await WorkoutImportGuard.ReadBoundedAsync(source, cancellationToken);
    try
    {
      Decode decoder = new();
      using (MemoryStream integrityStream = new(bytes, writable: false))
      {
        if (!decoder.IsFIT(integrityStream))
        {
          throw new WorkoutImportException("The FIT workout header is invalid.");
        }

        integrityStream.Position = 0;
        if (!decoder.CheckIntegrity(integrityStream))
        {
          throw new WorkoutImportException("The FIT workout CRC is invalid.");
        }
      }

      List<FileIdMesg> fileIds = [];
      List<WorkoutMesg> workouts = [];
      List<WorkoutStepMesg> steps = [];
      MesgBroadcaster broadcaster = new();
      broadcaster.FileIdMesgEvent += (_, args) => fileIds.Add(new FileIdMesg(args.mesg));
      broadcaster.WorkoutMesgEvent += (_, args) => workouts.Add(new WorkoutMesg(args.mesg));
      broadcaster.WorkoutStepMesgEvent += (_, args) => steps.Add(new WorkoutStepMesg(args.mesg));
      decoder.MesgEvent += broadcaster.OnMesg;
      decoder.MesgDefinitionEvent += broadcaster.OnMesgDefinition;

      using (MemoryStream decodeStream = new(bytes, writable: false))
      {
        if (!decoder.Read(decodeStream))
        {
          throw new WorkoutImportException("The FIT workout could not be decoded.");
        }
      }

      if (fileIds.Count != 1 || fileIds[0].GetType() != Dynastream.Fit.File.Workout)
      {
        throw new WorkoutImportException("The FIT file must contain exactly one Workout File Id message.");
      }

      if (workouts.Count != 1 || steps.Count == 0)
      {
        throw new WorkoutImportException("Exactly one FIT Workout message and at least one Workout Step message are required.");
      }
      WorkoutMesg workout = workouts[0];

      List<WorkoutImportWarning> warnings = [];
      Sport? sport = workout.GetSport();
      if (sport is not null and not Sport.Running and not Sport.Generic)
      {
        warnings.Add(new WorkoutImportWarning(
            "fit.non-running-sport",
            $"The FIT workout sport is '{sport}'. Only treadmill-relevant fields were imported."));
      }

      List<IndexedBlock> blocks = ParseSteps(steps, warnings);
      if (blocks.Count == 0)
      {
        throw new WorkoutImportException("The FIT workout contains no supported time or distance steps.");
      }

      ushort? declaredSteps = workout.GetNumValidSteps();
      if (declaredSteps is not null && declaredSteps != steps.Count)
      {
        warnings.Add(new WorkoutImportWarning(
            "fit.step-count-mismatch",
            $"The FIT workout declares {declaredSteps} steps but contains {steps.Count}; decoded messages were used."));
      }

      string title = workout.GetWktNameAsString();
      if (string.IsNullOrWhiteSpace(title))
      {
        title = Path.GetFileNameWithoutExtension(fileName);
      }

      if (string.IsNullOrWhiteSpace(title))
      {
        title = "Imported FIT workout";
      }

      string? description = EmptyToNull(workout.GetWktDescriptionAsString());
      WorkoutDefinition definition = new(1, title, description, blocks.Select(static item => item.Block).ToArray());
      return WorkoutImportGuard.Validate(Format, definition, warnings);
    }
    catch (FitException exception)
    {
      throw new WorkoutImportException("The FIT workout is malformed.", exception);
    }
    catch (ArgumentException exception)
    {
      throw new WorkoutImportException("The FIT workout contains invalid or out-of-range values.", exception);
    }
    catch (OverflowException exception)
    {
      throw new WorkoutImportException("The FIT workout contains a value that is too large.", exception);
    }
  }

  private static List<IndexedBlock> ParseSteps(
      List<WorkoutStepMesg> messages,
      List<WorkoutImportWarning> warnings)
  {
    List<(ushort Index, WorkoutStepMesg Message)> ordered = messages
        .Select(message => (
            Index: message.GetMessageIndex()
                ?? throw new WorkoutImportException("Every FIT workout step requires a message index."),
            Message: message))
        .OrderBy(static item => item.Index)
        .ToList();

    if (ordered.Select(static item => item.Index).Distinct().Count() != ordered.Count)
    {
      throw new WorkoutImportException("FIT workout step message indexes must be unique.");
    }

    List<IndexedBlock> blocks = [];
    foreach ((ushort index, WorkoutStepMesg message) in ordered)
    {
      WktStepDuration durationType = message.GetDurationType()
          ?? throw new WorkoutImportException($"FIT workout step {index} has no duration type.");
      if (durationType == WktStepDuration.RepeatUntilStepsCmplt)
      {
        CollapseRepeat(index, message, blocks, warnings);
        continue;
      }

      if (IsUnsupportedRepeat(durationType))
      {
        warnings.Add(new WorkoutImportWarning(
            "fit.repeat-until-unsupported",
            $"FIT step {index} uses conditional repeat '{durationType}', which is not supported; its preceding steps remain single-pass."));
        continue;
      }

      StepGoal? goal = ParseGoal(index, message, durationType, warnings);
      if (goal is null)
      {
        continue;
      }

      SpeedDirective speed = ParseSpeed(index, message, warnings);
      if (message.GetSecondaryTargetType() is { } secondary && secondary != WktStepTarget.Open)
      {
        warnings.Add(new WorkoutImportWarning(
            "fit.secondary-target-unsupported",
            $"FIT step {index} secondary target '{secondary}' is not supported and was ignored."));
      }

      WorkoutStep block = new(
          goal,
          speed,
          new FixedIncline(0),
          EmptyToNull(message.GetWktStepNameAsString()),
          EmptyToNull(message.GetNotesAsString()));
      blocks.Add(new IndexedBlock(index, index, block));
    }

    return blocks;
  }

  private static StepGoal? ParseGoal(
      ushort index,
      WorkoutStepMesg message,
      WktStepDuration durationType,
      List<WorkoutImportWarning> warnings)
  {
    switch (durationType)
    {
      case WktStepDuration.Time:
      case WktStepDuration.TimeOnly:
      case WktStepDuration.RepetitionTime:
        float? seconds = message.GetDurationTime();
        if (seconds is null or <= 0 || !float.IsFinite(seconds.Value))
        {
          throw new WorkoutImportException($"FIT workout step {index} has an invalid time duration.");
        }

        return new TimeGoal(TimeSpan.FromSeconds(seconds.Value));

      case WktStepDuration.Distance:
        float? meters = message.GetDurationDistance();
        if (meters is null or <= 0 || !float.IsFinite(meters.Value))
        {
          throw new WorkoutImportException($"FIT workout step {index} has an invalid distance duration.");
        }

        return new DistanceGoal(meters.Value / 1000d);

      default:
        warnings.Add(new WorkoutImportWarning(
            "fit.duration-unsupported",
            $"FIT step {index} duration '{durationType}' cannot be represented as time or distance and was skipped."));
        return null;
    }
  }

  private static SpeedDirective ParseSpeed(
      ushort index,
      WorkoutStepMesg message,
      List<WorkoutImportWarning> warnings)
  {
    WktStepTarget target = message.GetTargetType() ?? WktStepTarget.Open;
    switch (target)
    {
      case WktStepTarget.Open:
        warnings.Add(new WorkoutImportWarning(
            "fit.open-speed",
            $"FIT step {index} has no speed target and requires review before execution."));
        return new OpenSpeed();

      case WktStepTarget.Speed:
        float? low = message.GetCustomTargetSpeedLow();
        float? high = message.GetCustomTargetSpeedHigh();
        if (low is not null && high is not null && float.IsFinite(low.Value) && float.IsFinite(high.Value))
        {
          if (Math.Abs(low.Value - high.Value) < 0.0001f)
          {
            return new FixedSpeed(low.Value * 3.6d);
          }

          warnings.Add(new WorkoutImportWarning(
              "fit.speed-range-unsupported",
              $"FIT step {index} uses a speed range, which is not a ramp; it requires review and was left open."));
          return new OpenSpeed();
        }

        warnings.Add(new WorkoutImportWarning(
            "fit.speed-zone-unsupported",
            $"FIT step {index} references a predefined speed zone or incomplete range; it requires review and was left open."));
        return new OpenSpeed();

      case WktStepTarget.HeartRate:
        return ParseHeartRateSpeed(index, message, warnings);

      default:
        warnings.Add(new WorkoutImportWarning(
            "fit.target-unsupported",
            $"FIT step {index} target '{target}' is not supported and was left open."));
        return new OpenSpeed();
    }
  }

  private static SpeedDirective ParseHeartRateSpeed(
      ushort index,
      WorkoutStepMesg message,
      List<WorkoutImportWarning> warnings)
  {
    uint zone = message.GetTargetHrZone() ?? message.GetTargetValue() ?? 0;
    if (zone > 0)
    {
      if (zone > 10)
      {
        warnings.Add(new WorkoutImportWarning(
            "fit.hr-zone-invalid",
            $"FIT step {index} references HR zone {zone}, outside the supported 1-10 range; it was left open."));
        return new OpenSpeed();
      }

      warnings.Add(new WorkoutImportWarning(
          "fit.hr-speed-bounds-required",
          $"FIT step {index} HR zone has no treadmill speed bounds; zero bounds were retained and require editing before execution."));
      return new HeartRateZoneSpeed((int)zone, 0, 0, 0);
    }

    uint? low = message.GetCustomTargetHeartRateLow();
    uint? high = message.GetCustomTargetHeartRateHigh();
    if (low is null || high is null)
    {
      warnings.Add(new WorkoutImportWarning(
          "fit.hr-target-incomplete",
          $"FIT step {index} has an incomplete HR target and was left open."));
      return new OpenSpeed();
    }

    if (low <= 100 || high <= 100)
    {
      warnings.Add(new WorkoutImportWarning(
          "fit.hr-percent-unsupported",
          $"FIT step {index} uses percent-of-max HR, which cannot be preserved as a profile zone; it was left open."));
      return new OpenSpeed();
    }

    uint lowBpm = low.Value - 100;
    uint highBpm = high.Value - 100;
    if (lowBpm is 0 or > 250 || highBpm is 0 or > 250 || lowBpm > highBpm)
    {
      throw new WorkoutImportException($"FIT step {index} has invalid absolute HR bounds.");
    }

    warnings.Add(new WorkoutImportWarning(
        "fit.hr-speed-bounds-required",
        $"FIT step {index} HR range has no treadmill speed bounds; zero bounds were retained and require editing before execution."));
    return new HeartRateSpeed((ushort)lowBpm, (ushort)highBpm, 0, 0, 0);
  }

  private static void CollapseRepeat(
      ushort markerIndex,
      WorkoutStepMesg message,
      List<IndexedBlock> blocks,
      List<WorkoutImportWarning> warnings)
  {
    uint? startValue = message.GetDurationStep();
    uint? repeatValue = message.GetRepeatSteps() ?? message.GetTargetValue();
    if (startValue is null || startValue > ushort.MaxValue || repeatValue is null || repeatValue is 0 or > int.MaxValue)
    {
      throw new WorkoutImportException($"FIT repeat step {markerIndex} has invalid start/count values.");
    }

    ushort startIndex = (ushort)startValue.Value;
    int position = blocks.FindIndex(block => block.StartIndex == startIndex);
    if (position < 0)
    {
      throw new WorkoutImportException($"FIT repeat step {markerIndex} references missing step {startIndex}.");
    }

    List<WorkoutBlock> repeated = blocks.Skip(position).Select(static block => block.Block).ToList();
    if (repeated.Count == 0)
    {
      throw new WorkoutImportException($"FIT repeat step {markerIndex} has no preceding block to repeat.");
    }

    if (message.GetTargetType() is { } target && target != WktStepTarget.Open)
    {
      warnings.Add(new WorkoutImportWarning(
          "fit.repeat-target-ignored",
          $"FIT repeat step {markerIndex} target '{target}' was ignored; the repeat count was retained."));
    }

    blocks.RemoveRange(position, blocks.Count - position);
    blocks.Add(new IndexedBlock(startIndex, markerIndex, new WorkoutRepeat((int)repeatValue.Value, repeated)));
  }

  private static bool IsUnsupportedRepeat(WktStepDuration duration) => duration is
      WktStepDuration.RepeatUntilTime or
      WktStepDuration.RepeatUntilDistance or
      WktStepDuration.RepeatUntilCalories or
      WktStepDuration.RepeatUntilHrLessThan or
      WktStepDuration.RepeatUntilHrGreaterThan or
      WktStepDuration.RepeatUntilPowerLessThan or
      WktStepDuration.RepeatUntilPowerGreaterThan or
      WktStepDuration.RepeatUntilPowerLastLapLessThan or
      WktStepDuration.RepeatUntilMaxPowerLastLapLessThan or
      WktStepDuration.RepeatUntilTrainingPeaksTss;

  private static string? EmptyToNull(string? value) =>
      string.IsNullOrWhiteSpace(value) ? null : value.Trim();

  private sealed record IndexedBlock(ushort StartIndex, ushort EndIndex, WorkoutBlock Block);
}
