using System.Text;

namespace TreadmillRunner.Protocols.Polar;

public sealed class PolarPftpClient
{
  public const int MaximumExerciseBytes = 8 * 1024 * 1024;
  private const int MaximumControlBytes = 64 * 1024;
  private const int MaximumDirectoryEntries = 512;
  private const int MaximumDirectoryDepth = 4;
  private readonly IPolarPftpConnection _connection;
  private readonly TimeSpan _responseTimeout;

  public PolarPftpClient(IPolarPftpConnection connection, TimeSpan? responseTimeout = null)
  {
    _connection = connection ?? throw new ArgumentNullException(nameof(connection));
    _responseTimeout = responseTimeout ?? TimeSpan.FromSeconds(10);
    if (_responseTimeout <= TimeSpan.Zero || _responseTimeout > TimeSpan.FromMinutes(2))
      throw new ArgumentOutOfRangeException(nameof(responseTimeout));
  }

  public async ValueTask<PolarRecordingStatus> GetStatusAsync(CancellationToken cancellationToken = default)
  {
    ReadOnlyMemory<byte> payload = await QueryAsync(PolarPftpQueries.GetStatus, ReadOnlyMemory<byte>.Empty, MaximumControlBytes, cancellationToken).ConfigureAwait(false);
    if (payload.IsEmpty) return new(false);
    bool? isRecording = null;
    string? identifier = null;
    foreach (PolarProtobufField field in PolarPftpProtobuf.DecodeFields(payload.Span))
    {
      if (field.Number == 1 && field.WireType == 0) isRecording = field.Varint != 0;
      if (field.Number == 2 && field.WireType == 2) identifier = Encoding.UTF8.GetString(field.Bytes.Span);
    }
    return new PolarRecordingStatus(isRecording ?? throw new FormatException("Polar recording status omitted its required recording flag."), identifier);
  }

  public ValueTask<ReadOnlyMemory<byte>> StartAsync(
    string exerciseId,
    PolarRecordingSampleType sampleType = PolarRecordingSampleType.HeartRate,
    int intervalSeconds = 1,
    CancellationToken cancellationToken = default)
  {
    ValidateExerciseId(exerciseId);
    if (sampleType is not (PolarRecordingSampleType.HeartRate or PolarRecordingSampleType.RrInterval))
      throw new ArgumentOutOfRangeException(nameof(sampleType));
    if (sampleType == PolarRecordingSampleType.HeartRate && intervalSeconds is not (1 or 5))
      throw new ArgumentOutOfRangeException(nameof(intervalSeconds));
    if (sampleType == PolarRecordingSampleType.RrInterval) intervalSeconds = 1;

    byte[] interval = PolarPftpProtobuf.EncodeFields((3, (ulong)intervalSeconds));
    byte[] parameters = PolarPftpProtobuf.EncodeFields((1, (ulong)sampleType))
      .Concat(PolarPftpProtobuf.EncodeBytesField(2, interval))
      .Concat(PolarPftpProtobuf.EncodeBytesField(3, Encoding.UTF8.GetBytes(exerciseId)))
      .ToArray();
    return QueryAsync(PolarPftpQueries.Start, parameters, MaximumControlBytes, cancellationToken);
  }

  public ValueTask<ReadOnlyMemory<byte>> StopAsync(CancellationToken cancellationToken = default) =>
    QueryAsync(PolarPftpQueries.Stop, ReadOnlyMemory<byte>.Empty, MaximumControlBytes, cancellationToken);

  public async ValueTask<IReadOnlyList<PolarExerciseSummary>> ListExercisesAsync(CancellationToken cancellationToken = default)
  {
    var results = new List<PolarExerciseSummary>();
    int entryCount = 0;
    await ReadDirectoryAsync("/", 0, results, () => ++entryCount, cancellationToken).ConfigureAwait(false);
    return results;
  }

  public async ValueTask<PolarExerciseSamples> FetchExerciseAsync(string path, CancellationToken cancellationToken = default)
  {
    ValidateSamplePath(path);
    ReadOnlyMemory<byte> payload = await GetPathAsync(path, MaximumExerciseBytes, cancellationToken).ConfigureAwait(false);
    int intervalSeconds = 0;
    var heartRates = new List<ushort>();
    var rrIntervals = new List<uint>();
    foreach (PolarProtobufField field in PolarPftpProtobuf.DecodeFields(payload.Span))
    {
      if (field.Number == 1 && field.WireType == 2)
      {
        intervalSeconds = DecodeWholeSeconds(field.Bytes.Span);
      }
      else if (field.Number == 2)
      {
        foreach (ulong value in ReadRepeatedVarints(field))
        {
          if (value > ushort.MaxValue) throw new FormatException("Polar heart-rate sample is outside its supported range.");
          heartRates.Add((ushort)value);
        }
      }
      else if (field.Number == 28 && field.WireType == 2)
      {
        foreach (PolarProtobufField rrField in PolarPftpProtobuf.DecodeFields(field.Bytes.Span).Where(candidate => candidate.Number == 1))
          foreach (ulong value in ReadRepeatedVarints(rrField))
          {
            if (value > uint.MaxValue) throw new FormatException("Polar RR interval is outside its supported range.");
            rrIntervals.Add((uint)value);
          }
      }
    }
    if (intervalSeconds <= 0) throw new FormatException("Polar exercise samples omitted a valid recording interval.");
    PolarRecordingSampleType type = rrIntervals.Count > 0 ? PolarRecordingSampleType.RrInterval : PolarRecordingSampleType.HeartRate;
    return new(path, payload, type, intervalSeconds, heartRates, rrIntervals);
  }

  private static int DecodeWholeSeconds(ReadOnlySpan<byte> payload)
  {
    ulong hours = 0, minutes = 0, seconds = 0, millis = 0;
    foreach (PolarProtobufField field in PolarPftpProtobuf.DecodeFields(payload))
    {
      if (field.WireType != 0) continue;
      switch (field.Number)
      {
        case 1: hours = field.Varint; break;
        case 2: minutes = field.Varint; break;
        case 3: seconds = field.Varint; break;
        case 4: millis = field.Varint; break;
      }
    }
    ulong totalMilliseconds = checked(((hours * 60 + minutes) * 60 + seconds) * 1000 + millis);
    if (totalMilliseconds == 0 || totalMilliseconds % 1000 != 0)
      throw new FormatException("Polar exercise samples use an unsupported sub-second recording interval.");
    return checked((int)(totalMilliseconds / 1000));
  }

  public ValueTask<ReadOnlyMemory<byte>> RemoveExerciseAsync(string path, CancellationToken cancellationToken = default)
  {
    ValidateSamplePath(path);
    return ExchangeOperationAsync(3, path, MaximumControlBytes, cancellationToken);
  }

  private async ValueTask ReadDirectoryAsync(
    string path,
    int depth,
    List<PolarExerciseSummary> results,
    Func<int> incrementEntryCount,
    CancellationToken cancellationToken)
  {
    if (depth > MaximumDirectoryDepth) throw new InvalidOperationException("Polar exercise directory exceeds the traversal depth limit.");
    ReadOnlyMemory<byte> payload = await GetPathAsync(path, MaximumControlBytes, cancellationToken).ConfigureAwait(false);
    foreach (PolarProtobufField outer in PolarPftpProtobuf.DecodeFields(payload.Span).Where(field => field.Number == 1 && field.WireType == 2))
    {
      if (incrementEntryCount() > MaximumDirectoryEntries) throw new InvalidOperationException("Polar exercise directory exceeds the entry limit.");
      string? name = null;
      long size = 0;
      foreach (PolarProtobufField entry in PolarPftpProtobuf.DecodeFields(outer.Bytes.Span))
      {
        if (entry.Number == 1 && entry.WireType == 2) name = Encoding.UTF8.GetString(entry.Bytes.Span);
        if (entry.Number == 2 && entry.WireType == 0) size = checked((long)entry.Varint);
      }
      ValidateDirectoryEntry(name);
      string entryName = name!;
      string childPath = path + entryName;
      if (entryName.EndsWith("/", StringComparison.Ordinal))
        await ReadDirectoryAsync(childPath, depth + 1, results, incrementEntryCount, cancellationToken).ConfigureAwait(false);
      else if (entryName.Equals("SAMPLES.BPB", StringComparison.OrdinalIgnoreCase))
        results.Add(new PolarExerciseSummary(childPath, size));
    }
  }

  private ValueTask<ReadOnlyMemory<byte>> GetPathAsync(string path, int maximumBytes, CancellationToken cancellationToken) =>
    ExchangeOperationAsync(0, path, maximumBytes, cancellationToken);

  private ValueTask<ReadOnlyMemory<byte>> ExchangeOperationAsync(int command, string path, int maximumBytes, CancellationToken cancellationToken)
  {
    ValidatePath(path);
    byte[] operation = PolarPftpProtobuf.EncodeFields((1, (ulong)command))
      .Concat(PolarPftpProtobuf.EncodeBytesField(2, Encoding.UTF8.GetBytes(path))).ToArray();
    return _connection.ExchangeAsync(PolarPftpRfc60Codec.EncodeOperation(operation), _responseTimeout, maximumBytes, cancellationToken);
  }

  private ValueTask<ReadOnlyMemory<byte>> QueryAsync(ushort query, ReadOnlyMemory<byte> parameters, int maximumBytes, CancellationToken cancellationToken) =>
    _connection.ExchangeAsync(PolarPftpRfc60Codec.EncodeQuery(query, parameters.Span), _responseTimeout, maximumBytes, cancellationToken);

  private static IEnumerable<ulong> ReadRepeatedVarints(PolarProtobufField field)
  {
    if (field.WireType == 0) { yield return field.Varint; yield break; }
    if (field.WireType != 2) throw new FormatException("Polar sample field uses an unsupported protobuf wire type.");
    int offset = 0;
    while (offset < field.Bytes.Length) yield return PolarPftpProtobuf.ReadVarint(field.Bytes.Span, ref offset);
  }

  private static void ValidateExerciseId(string identifier)
  {
    if (string.IsNullOrWhiteSpace(identifier) || identifier.Length > 64 || identifier.Contains('/') || identifier.Contains('\\') || identifier.Contains('\0'))
      throw new ArgumentException("A Polar exercise identifier must contain 1 through 64 path-safe characters.", nameof(identifier));
  }

  private static void ValidateSamplePath(string path)
  {
    ValidatePath(path);
    if (!path.EndsWith("/SAMPLES.BPB", StringComparison.OrdinalIgnoreCase))
      throw new ArgumentException("A Polar exercise path must identify SAMPLES.BPB.", nameof(path));
  }

  private static void ValidatePath(string path)
  {
    if (string.IsNullOrWhiteSpace(path) || path.Length > 512 || path[0] != '/' || path.Contains('\\') || path.Contains('\0') || path.Contains("//", StringComparison.Ordinal))
      throw new ArgumentException("A bounded absolute Polar path is required.", nameof(path));
    if (path.Split('/', StringSplitOptions.RemoveEmptyEntries).Any(segment => segment is "." or ".."))
      throw new ArgumentException("Polar paths cannot traverse parent directories.", nameof(path));
  }

  private static void ValidateDirectoryEntry(string? name)
  {
    if (string.IsNullOrWhiteSpace(name) || name.Length > 128 ||
        (name.Contains('/') && !name.EndsWith("/", StringComparison.Ordinal)) ||
        name.TrimEnd('/') is "." or ".." || name.Contains('\\') || name.Contains('\0'))
      throw new FormatException("Polar directory contains an invalid entry name.");
  }
}
