using System.Globalization;

namespace TreadmillRunner.Web.Planning;

internal static class WorkoutSegmentTimeline
{
  public static IReadOnlyList<WorkoutSegmentTiming> Expand(IReadOnlyList<WorkoutBlockInput> blocks)
  {
    var result = new List<WorkoutSegmentTiming>();
    TimeSpan? elapsed = TimeSpan.Zero;
    ExpandInto(blocks);
    return result;

    void ExpandInto(IReadOnlyList<WorkoutBlockInput> source)
    {
      foreach (WorkoutBlockInput block in source)
      {
        if (IsRepeat(block))
        {
          for (int repetition = 0; repetition < Math.Max(1, block.Repetitions); repetition++)
          {
            ExpandInto(block.Blocks);
          }

          continue;
        }

        result.Add(new WorkoutSegmentTiming(result.Count + 1, block, elapsed));
        elapsed = Advance(elapsed, block);
      }
    }
  }

  public static TimeSpan? Advance(TimeSpan? start, WorkoutBlockInput block)
  {
    if (start is null)
    {
      return null;
    }

    if (IsRepeat(block))
    {
      TimeSpan? elapsed = start;
      for (int repetition = 0; repetition < Math.Max(1, block.Repetitions); repetition++)
      {
        foreach (WorkoutBlockInput child in block.Blocks)
        {
          elapsed = Advance(elapsed, child);
        }
      }

      return elapsed;
    }

    if (!string.Equals(block.GoalKind, "time", StringComparison.OrdinalIgnoreCase) ||
        !double.IsFinite(block.GoalValue) ||
        block.GoalValue < 0 ||
        block.GoalValue > TimeSpan.MaxValue.TotalMinutes)
    {
      return null;
    }

    TimeSpan duration = TimeSpan.FromMinutes(block.GoalValue);
    return start.Value <= TimeSpan.MaxValue - duration
      ? start.Value + duration
      : null;
  }

  public static string FormatStart(TimeSpan? start)
  {
    if (start is null)
    {
      return "—";
    }

    long totalSeconds = checked((long)Math.Round(start.Value.TotalSeconds, MidpointRounding.AwayFromZero));
    long hours = totalSeconds / 3600;
    long minutes = totalSeconds % 3600 / 60;
    long seconds = totalSeconds % 60;
    return string.Create(CultureInfo.InvariantCulture, $"{hours}:{minutes:00}:{seconds:00}");
  }

  private static bool IsRepeat(WorkoutBlockInput block) =>
    string.Equals(block.Kind, "repeat", StringComparison.OrdinalIgnoreCase);
}

internal sealed record WorkoutSegmentTiming(
  int Number,
  WorkoutBlockInput Block,
  TimeSpan? Start);
