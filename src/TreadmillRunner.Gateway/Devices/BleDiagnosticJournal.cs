using System.Reflection;
using System.Text.Json;
using System.Threading.Channels;

namespace TreadmillRunner.Gateway.Devices;

// Deliberately contains no payloads, device addresses, names, or exception messages.
public sealed record BleDiagnosticEvent(
  DateTimeOffset AtUtc, Guid EnrollmentId, string Role, long Generation, string Phase,
  string? Failure = null, int? HResult = null, int? Samples = null,
  double? LastValidAgeSeconds = null, double? RetrySeconds = null,
  string? Quality = null, Guid? ProfileId = null, double? RecoverySeconds = null,
  double? MaximumValidIntervalSeconds = null);

public sealed class BleDiagnosticJournal(string directory, ILogger<BleDiagnosticJournal> logger) : BackgroundService
{
  private const long MaximumFileBytes = 2 * 1024 * 1024;
  private const int RetainedFiles = 8;
  private static readonly string ApplicationVersion = typeof(BleDiagnosticJournal).Assembly
    .GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion ?? "unknown";
  private readonly Channel<BleDiagnosticEvent> _events = Channel.CreateBounded<BleDiagnosticEvent>(
    new BoundedChannelOptions(2048) { SingleReader = true, FullMode = BoundedChannelFullMode.Wait });
  private long _dropped;
  private long _storageFailures;
  private long _lastWrittenTicks;
  public long DroppedEvents => Interlocked.Read(ref _dropped);
  public long StorageFailures => Interlocked.Read(ref _storageFailures);
  public DateTimeOffset? LastWriteAtUtc => Interlocked.Read(ref _lastWrittenTicks) is > 0 and var ticks
    ? new DateTimeOffset(ticks, TimeSpan.Zero) : null;

  public void Record(BleDiagnosticEvent entry)
  {
    if (!_events.Writer.TryWrite(entry)) Interlocked.Increment(ref _dropped);
  }

  protected override async Task ExecuteAsync(CancellationToken stoppingToken)
  {
    try
    {
      await foreach (BleDiagnosticEvent entry in _events.Reader.ReadAllAsync(stoppingToken))
        await WriteAsync(entry);
    }
    catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { }
    finally
    {
      while (_events.Reader.TryRead(out BleDiagnosticEvent? entry)) await WriteAsync(entry);
    }
  }

  public override async Task StopAsync(CancellationToken cancellationToken)
  {
    _events.Writer.TryComplete();
    // Complete and drain before cancelling BackgroundService. Otherwise a stop
    // immediately after startup can cancel its scheduled delegate before it runs.
    try
    {
      if (ExecuteTask is { } execution) await execution.WaitAsync(cancellationToken);
    }
    finally { await base.StopAsync(cancellationToken); }
  }

  private async Task WriteAsync(BleDiagnosticEvent entry)
  {
    try
    {
      Directory.CreateDirectory(directory);
      string path = Path.Combine(directory, "bluetooth.jsonl");
      if (File.Exists(path) && new FileInfo(path).Length >= MaximumFileBytes)
      {
        for (int index = RetainedFiles - 1; index >= 1; index--)
        {
          string source = index == 1 ? path : Path.Combine(directory, $"bluetooth.{index - 1}.jsonl");
          string destination = Path.Combine(directory, $"bluetooth.{index}.jsonl");
          if (File.Exists(source)) File.Move(source, destination, overwrite: true);
        }
      }
      await File.AppendAllTextAsync(path, JsonSerializer.Serialize(new
      {
        Event = entry,
        DroppedEvents,
        ProcessId = Environment.ProcessId,
        ApplicationVersion,
      }) + Environment.NewLine);
      Interlocked.Exchange(ref _lastWrittenTicks, DateTimeOffset.UtcNow.Ticks);
    }
    catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
    {
      Interlocked.Increment(ref _storageFailures);
      long dropped = Interlocked.Increment(ref _dropped);
      if (dropped == 1 || dropped % 100 == 0)
        logger.LogWarning("Bluetooth diagnostic storage unavailable; {DroppedEvents} events lost.", dropped);
    }
  }
}
