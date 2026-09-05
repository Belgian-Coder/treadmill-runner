using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using TreadmillRunner.Gateway.Devices;

namespace TreadmillRunner.IntegrationTests;

public sealed class BleDiagnosticJournalTests
{
  [Fact]
  public async Task Shutdown_flushes_correlated_events_and_rotates_existing_large_file()
  {
    string directory = Path.Combine(Path.GetTempPath(), $"ble-journal-{Guid.NewGuid():N}");
    Directory.CreateDirectory(directory);
    try
    {
      string path = Path.Combine(directory, "bluetooth.jsonl");
      await File.WriteAllTextAsync(path, new string('x', 2 * 1024 * 1024));
      for (int index = 1; index <= 31; index++)
        await File.WriteAllTextAsync(Path.Combine(directory, $"bluetooth.{index}.jsonl"), "old evidence");
      using var journal = new BleDiagnosticJournal(directory, NullLogger<BleDiagnosticJournal>.Instance);
      Guid enrollment = Guid.NewGuid();
      journal.Record(new(DateTimeOffset.UtcNow, enrollment, "HeartRate", 42,
        "attempt-failed", "NativeDisconnected", Samples: 83, LastValidAgeSeconds: 1.5));
      await journal.StartAsync(CancellationToken.None);
      await journal.StopAsync(CancellationToken.None);

      Assert.True(File.Exists(Path.Combine(directory, "bluetooth.1.jsonl")));
      string[] lines = await File.ReadAllLinesAsync(path);
      Assert.Equal(2, lines.Length);
      using JsonDocument health = JsonDocument.Parse(lines[0]);
      JsonElement healthEvent = health.RootElement.GetProperty("Event");
      Assert.Equal("journal-started", healthEvent.GetProperty("Phase").GetString());
      Assert.False(healthEvent.TryGetProperty("Failure", out _));
      Assert.False(healthEvent.TryGetProperty("SessionId", out _));
      using JsonDocument record = JsonDocument.Parse(lines[1]);
      JsonElement entry = record.RootElement.GetProperty("Event");
      Assert.Equal(enrollment, entry.GetProperty("EnrollmentId").GetGuid());
      Assert.Equal(42, entry.GetProperty("Generation").GetInt64());
      Assert.Equal(1.5, entry.GetProperty("LastValidAgeSeconds").GetDouble());
      Assert.Equal(0, journal.DroppedEvents);
      Assert.NotNull(journal.LastWriteAtUtc);
      Assert.Equal(32, Directory.GetFiles(directory).Length);
    }
    finally { Directory.Delete(directory, recursive: true); }
  }

  [Fact]
  public void Queue_pressure_is_bounded_and_reports_lost_evidence()
  {
    using var journal = new BleDiagnosticJournal("unused", NullLogger<BleDiagnosticJournal>.Instance);
    for (int index = 0; index < 2050; index++)
      journal.Record(new(DateTimeOffset.UtcNow, Guid.Empty, "HeartRate", index, "Connecting"));
    Assert.Equal(2, journal.DroppedEvents);
  }

  [Fact]
  public async Task Unavailable_storage_counts_loss_without_stopping_the_service()
  {
    string path = Path.GetTempFileName();
    try
    {
      using var journal = new BleDiagnosticJournal(path, NullLogger<BleDiagnosticJournal>.Instance);
      journal.Record(new(DateTimeOffset.UtcNow, Guid.Empty, "HeartRate", 1, "Connecting"));
      await journal.StartAsync(CancellationToken.None);
      await journal.StopAsync(CancellationToken.None);
      Assert.Equal(2, journal.StorageFailures);
      Assert.Equal(2, journal.DroppedEvents);
      Assert.Null(journal.LastWriteAtUtc);
    }
    finally { File.Delete(path); }
  }
}
