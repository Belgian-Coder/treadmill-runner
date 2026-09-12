using System.Text;

namespace TreadmillRunner.PolarH10Simulator;

public sealed class PolarH10SimulatorOptions
{
  public string DeviceName { get; init; } = "Polar H10 Simulator";
  public ushort HeartRate { get; init; } = 72;
  public byte BatteryPercent { get; init; } = 100;
  public int FrameSize { get; init; } = PolarH10SimulatorFrameCodec.DefaultFrameSize;
  public int IntervalSeconds { get; init; } = 1;
  public string SeedExerciseId { get; init; } = "simulated-run";
  public bool IncludeSeedRecording { get; init; } = true;
  public IReadOnlyList<ushort> HeartRateSamples { get; init; } = Array.Empty<ushort>();
  public IReadOnlyList<uint> RrIntervalsMilliseconds { get; init; } = Array.Empty<uint>();
  public TimeSpan NotificationDelay { get; init; } = TimeSpan.Zero;
  public int DropEveryNthNotification { get; init; }
  public int DisconnectAfterNotifications { get; init; }
  public bool MtuWriteWithoutResponseOnly { get; init; }

  public void Validate()
  {
    if (string.IsNullOrWhiteSpace(DeviceName) || DeviceName.Length > 24)
      throw new ArgumentException("The advertised display name must contain 1 through 24 characters.", nameof(DeviceName));
    if (HeartRate is < 1 or > 250) throw new ArgumentOutOfRangeException(nameof(HeartRate));
    if (BatteryPercent > 100) throw new ArgumentOutOfRangeException(nameof(BatteryPercent));
    if (FrameSize is < 3 or > PolarH10SimulatorFrameCodec.MaximumFrameSize)
      throw new ArgumentOutOfRangeException(nameof(FrameSize));
    if (IntervalSeconds is not (1 or 5)) throw new ArgumentOutOfRangeException(nameof(IntervalSeconds));
    ValidateExerciseId(SeedExerciseId);
    if (HeartRateSamples is null || HeartRateSamples.Count > 4096)
      throw new ArgumentException("At most 4096 heart-rate samples are supported.", nameof(HeartRateSamples));
    if (HeartRateSamples.Any(value => value is < 1 or > 250))
      throw new ArgumentException("Heart-rate samples must be between 1 and 250 BPM.", nameof(HeartRateSamples));
    if (RrIntervalsMilliseconds is null || RrIntervalsMilliseconds.Count > 4096)
      throw new ArgumentException("At most 4096 RR samples are supported.", nameof(RrIntervalsMilliseconds));
    if (RrIntervalsMilliseconds.Any(value => value == 0))
      throw new ArgumentException("RR intervals must be positive milliseconds.", nameof(RrIntervalsMilliseconds));
    if (NotificationDelay < TimeSpan.Zero || NotificationDelay > TimeSpan.FromMinutes(2))
      throw new ArgumentOutOfRangeException(nameof(NotificationDelay));
    if (DropEveryNthNotification < 0) throw new ArgumentOutOfRangeException(nameof(DropEveryNthNotification));
    if (DisconnectAfterNotifications < 0) throw new ArgumentOutOfRangeException(nameof(DisconnectAfterNotifications));
  }

  internal static void ValidateExerciseId(string identifier)
  {
    if (string.IsNullOrWhiteSpace(identifier) || identifier.Length > 64 ||
        identifier.Contains('/') || identifier.Contains('\\') || identifier.Contains('\0'))
      throw new ArgumentException("A Polar exercise identifier must contain 1 through 64 path-safe characters.", nameof(identifier));
  }
}

public sealed record PolarH10SimulatorEmission(
  PolarH10SimulatorCharacteristic Characteristic,
  byte[] Value,
  int AttemptNumber,
  bool Dropped,
  bool DisconnectRequested);

/// <summary>
/// Deterministic, transport-independent Polar H10 behavior used by the Windows
/// peripheral adapter and by unit tests. It owns no treadmill or DI dependencies.
/// </summary>
public sealed class PolarH10SimulatorStateMachine
{
  public const ushort InvalidRequestError = 1;
  public const ushort UnsupportedCommandError = 2;
  public const ushort InvalidParametersError = 3;
  public const ushort NotFoundError = 4;
  public const ushort BusyError = 5;

  private readonly PolarH10SimulatorOptions _options;
  private readonly Dictionary<string, SimulatedRecording> _recordings = new(StringComparer.OrdinalIgnoreCase);
  private readonly List<byte[]> _requestPackets = [];
  private byte _expectedRequestSequence;
  private string? _activeExerciseId;
  private int _notificationAttempts;
  private int _heartRateIndex;

  public PolarH10SimulatorStateMachine(PolarH10SimulatorOptions? options = null)
  {
    _options = options ?? new PolarH10SimulatorOptions();
    _options.Validate();
    if (_options.IncludeSeedRecording)
      AddRecording(_options.SeedExerciseId, _options.IntervalSeconds, false);
  }

  public PolarH10SimulatorOptions Options => _options;
  public bool IsRecording => _activeExerciseId is not null;
  public string? ActiveExerciseId => _activeExerciseId;
  public int NotificationAttempts => _notificationAttempts;
  public bool DisconnectRequested { get; private set; }

  public void BeginConnection()
  {
    _requestPackets.Clear();
    _expectedRequestSequence = 0;
    _notificationAttempts = 0;
    _heartRateIndex = 0;
    DisconnectRequested = false;
  }

  /// <summary>Accepts one write to either PFTP write characteristic.</summary>
  public IReadOnlyList<PolarH10SimulatorEmission> AcceptHostPacket(ReadOnlySpan<byte> packet)
  {
    if (DisconnectRequested) return Array.Empty<PolarH10SimulatorEmission>();

    try
    {
      PolarH10SimulatorFrame frame = PolarH10SimulatorFrameCodec.Decode(packet);
      bool first = _requestPackets.Count == 0;
      byte expected = first ? (byte)0 : _expectedRequestSequence;
      if (frame.Sequence != expected || frame.Next != !first ||
          frame.Status is PolarH10SimulatorFrameStatus.ResponseOrError)
        return Error(InvalidRequestError);

      _requestPackets.Add(packet.ToArray());
      _expectedRequestSequence = (byte)((frame.Sequence + 1) & 0x0f);
      if (frame.Status == PolarH10SimulatorFrameStatus.More) return Array.Empty<PolarH10SimulatorEmission>();

      byte[] request = PolarH10SimulatorFrameCodec.Reassemble(_requestPackets.Select(static bytes => (ReadOnlyMemory<byte>)bytes));
      _requestPackets.Clear();
      _expectedRequestSequence = 0;
      return HandleMessage(request);
    }
    catch (PolarH10SimulatorProtocolException exception)
    {
      return Error(exception.Code);
    }
    catch (Exception exception) when (exception is FormatException or ArgumentException or OverflowException)
    {
      return Error(InvalidRequestError);
    }
  }

  public PolarH10SimulatorEmission CreateHeartRateNotification()
  {
    return CreateEmission(PolarH10SimulatorCharacteristic.HeartRate, CreateHeartRateMeasurement());
  }

  public byte[] PeekHeartRateMeasurement()
  {
    IReadOnlyList<ushort> samples = _options.HeartRateSamples.Count == 0
      ? new[] { _options.HeartRate }
      : _options.HeartRateSamples;
    ushort value = samples[_heartRateIndex % samples.Count];
    return value <= byte.MaxValue
      ? new[] { (byte)0, (byte)value }
      : new[] { (byte)1, (byte)value, (byte)(value >> 8) };
  }

  public byte[] CreateHeartRateMeasurement()
  {
    IReadOnlyList<ushort> samples = _options.HeartRateSamples.Count == 0
      ? new[] { _options.HeartRate }
      : _options.HeartRateSamples;
    ushort value = samples[_heartRateIndex++ % samples.Count];
    return value <= byte.MaxValue
      ? new[] { (byte)0, (byte)value }
      : new[] { (byte)1, (byte)value, (byte)(value >> 8) };
  }

  private IReadOnlyList<PolarH10SimulatorEmission> HandleMessage(ReadOnlySpan<byte> message)
  {
    if (message.Length < 2) return Error(InvalidRequestError);
    if ((message[1] & 0x80) != 0)
    {
      return HandleQuery(PolarH10SimulatorFrameCodec.ReadQueryId(message), message[2..]);
    }

    (ReadOnlyMemory<byte> operation, ReadOnlyMemory<byte> data) =
      PolarH10SimulatorFrameCodec.DecodeOperation(message.ToArray());
    if (!data.IsEmpty) return Error(InvalidParametersError);
    int command = 0;
    string? path = null;
    foreach (PolarH10SimulatorProtobufField field in PolarH10SimulatorProtobuf.DecodeFields(operation.Span))
    {
      if (field.Number == 1 && field.WireType == 0) command = checked((int)field.Varint);
      else if (field.Number == 2 && field.WireType == 2) path = Encoding.UTF8.GetString(field.Bytes.Span);
    }
    if (path is null) return Error(InvalidParametersError);
    return command switch
    {
      0 => HandleRead(path),
      3 => HandleRemove(path),
      _ => Error(UnsupportedCommandError),
    };
  }

  private IReadOnlyList<PolarH10SimulatorEmission> HandleQuery(ushort query, ReadOnlySpan<byte> parameters)
  {
    return query switch
    {
      14 => HandleStart(parameters),
      15 => HandleStop(parameters),
      16 => parameters.IsEmpty ? Success(EncodeStatus()) : Error(InvalidParametersError),
      _ => Error(UnsupportedCommandError),
    };
  }

  private IReadOnlyList<PolarH10SimulatorEmission> HandleStart(ReadOnlySpan<byte> parameters)
  {
    ulong? sampleType = null;
    byte[]? intervalBytes = null;
    string? exerciseId = null;
    foreach (PolarH10SimulatorProtobufField field in PolarH10SimulatorProtobuf.DecodeFields(parameters))
    {
      if (field.Number == 1 && field.WireType == 0) sampleType = field.Varint;
      else if (field.Number == 2 && field.WireType == 2) intervalBytes = field.Bytes.ToArray();
      else if (field.Number == 3 && field.WireType == 2) exerciseId = Encoding.UTF8.GetString(field.Bytes.Span);
    }
    if (sampleType is not (1 or 16) || intervalBytes is null || exerciseId is null || IsRecording)
      return Error(IsRecording ? BusyError : InvalidParametersError);
    PolarH10SimulatorOptions.ValidateExerciseId(exerciseId);
    int interval = DecodeInterval(intervalBytes, sampleType == 16 ? 1UL : (ulong)_options.IntervalSeconds);
    if (interval <= 0) return Error(InvalidParametersError);
    if (sampleType == 1 && interval is not (1 or 5)) return Error(InvalidParametersError);

    _activeExerciseId = exerciseId;
    _activeSampleType = sampleType == 16 ? 16 : 1;
    _activeIntervalSeconds = sampleType == 16 ? 1 : interval;
    return Success(Array.Empty<byte>());
  }

  private IReadOnlyList<PolarH10SimulatorEmission> HandleStop(ReadOnlySpan<byte> parameters)
  {
    if (!parameters.IsEmpty) return Error(InvalidParametersError);
    if (_activeExerciseId is not null)
    {
      AddRecording(_activeExerciseId, _activeIntervalSeconds, _activeSampleType == 16);
      _activeExerciseId = null;
    }
    return Success(Array.Empty<byte>());
  }

  private IReadOnlyList<PolarH10SimulatorEmission> HandleRead(string path)
  {
    ValidatePath(path);
    if (path == "/")
    {
      byte[] directory = EncodeDirectory(_recordings.Values.OrderBy(static value => value.Identifier, StringComparer.OrdinalIgnoreCase));
      return Success(directory);
    }
    if (path.EndsWith("/", StringComparison.Ordinal))
    {
      string directoryIdentifier = path[1..^1];
      if (!_recordings.TryGetValue(directoryIdentifier, out SimulatedRecording? directoryRecording)) return Error(NotFoundError);
      return Success(EncodeDirectoryEntry("SAMPLES.BPB", directoryRecording.SizeBytes));
    }
    if (!path.EndsWith("/SAMPLES.BPB", StringComparison.OrdinalIgnoreCase)) return Error(NotFoundError);
    string identifier = path[1..^("/SAMPLES.BPB".Length)];
    if (!_recordings.TryGetValue(identifier, out SimulatedRecording? recording)) return Error(NotFoundError);
    return Success(recording.Payload);
  }

  private IReadOnlyList<PolarH10SimulatorEmission> HandleRemove(string path)
  {
    ValidatePath(path);
    if (!path.EndsWith("/SAMPLES.BPB", StringComparison.OrdinalIgnoreCase)) return Error(NotFoundError);
    string identifier = path[1..^"/SAMPLES.BPB".Length];
    if (string.Equals(identifier, _activeExerciseId, StringComparison.OrdinalIgnoreCase)) return Error(BusyError);
    return _recordings.Remove(identifier) ? Success(Array.Empty<byte>()) : Error(NotFoundError);
  }

  private IReadOnlyList<PolarH10SimulatorEmission> Success(byte[] payload) =>
    PolarH10SimulatorFrameCodec.EncodeResponse(payload, _options.FrameSize)
      .Select(packet => CreateEmission(PolarH10SimulatorCharacteristic.Mtu, packet))
      .ToArray();

  private IReadOnlyList<PolarH10SimulatorEmission> Error(ushort code)
  {
    _requestPackets.Clear();
    _expectedRequestSequence = 0;
    return [CreateEmission(PolarH10SimulatorCharacteristic.Mtu, PolarH10SimulatorFrameCodec.EncodeError(code))];
  }

  private PolarH10SimulatorEmission CreateEmission(PolarH10SimulatorCharacteristic characteristic, byte[] value)
  {
    int attempt = checked(++_notificationAttempts);
    bool dropped = _options.DropEveryNthNotification > 0 && attempt % _options.DropEveryNthNotification == 0;
    bool disconnect = _options.DisconnectAfterNotifications > 0 && attempt >= _options.DisconnectAfterNotifications;
    DisconnectRequested |= disconnect;
    return new(characteristic, value, attempt, dropped, disconnect);
  }

  private void AddRecording(string identifier, int intervalSeconds, bool rr)
  {
    byte[] payload = EncodeSamples(rr, intervalSeconds);
    _recordings[identifier] = new(identifier, payload.Length, payload);
  }

  private byte[] EncodeStatus()
  {
    var fields = new List<byte[]>(2) { PolarH10SimulatorProtobuf.EncodeFields((1, IsRecording ? 1UL : 0UL)) };
    if (_activeExerciseId is not null)
      fields.Add(PolarH10SimulatorProtobuf.EncodeBytesField(2, Encoding.UTF8.GetBytes(_activeExerciseId)));
    return fields.SelectMany(static bytes => bytes).ToArray();
  }

  private byte[] EncodeDirectory(IEnumerable<SimulatedRecording> recordings)
  {
    return recordings.SelectMany(recording => EncodeDirectoryEntry(recording.Identifier + "/", recording.SizeBytes))
      .ToArray();
  }

  private static byte[] EncodeDirectoryEntry(string name, long sizeBytes) =>
    PolarH10SimulatorProtobuf.EncodeBytesField(1,
      PolarH10SimulatorProtobuf.EncodeBytesField(1, Encoding.UTF8.GetBytes(name))
        .Concat(PolarH10SimulatorProtobuf.EncodeFields((2, (ulong)sizeBytes))).ToArray());

  private byte[] EncodeSamples(bool rr, int intervalSeconds)
  {
    byte[] duration = PolarH10SimulatorProtobuf.EncodeBytesField(1,
      PolarH10SimulatorProtobuf.EncodeFields((3, (ulong)intervalSeconds)));
    if (rr)
    {
      IReadOnlyList<uint> intervals = _options.RrIntervalsMilliseconds.Count == 0
        ? new uint[] { 1000, 1000, 1000 }
        : _options.RrIntervalsMilliseconds;
      byte[] rrPayload = PolarH10SimulatorProtobuf.EncodePackedVarintsField(1, intervals.Select(static value => (ulong)value));
      return duration.Concat(PolarH10SimulatorProtobuf.EncodeBytesField(28, rrPayload)).ToArray();
    }
    IReadOnlyList<ushort> samples = _options.HeartRateSamples.Count == 0
      ? new[] { _options.HeartRate }
      : _options.HeartRateSamples;
    return duration.Concat(PolarH10SimulatorProtobuf.EncodePackedVarintsField(2,
      samples.Select(static value => (ulong)value))).ToArray();
  }

  private static int DecodeInterval(ReadOnlySpan<byte> payload, ulong sampleTypeDefault)
  {
    ulong seconds = sampleTypeDefault;
    bool found = false;
    foreach (PolarH10SimulatorProtobufField field in PolarH10SimulatorProtobuf.DecodeFields(payload))
      if (field.Number == 3 && field.WireType == 0)
      {
        seconds = field.Varint;
        found = true;
      }
    if (!found) return 0;
    return checked((int)seconds);
  }

  private static void ValidatePath(string path)
  {
    if (string.IsNullOrWhiteSpace(path) || path.Length > 512 || path[0] != '/' || path.Contains('\\') ||
        path.Contains('\0') || path.Contains("//", StringComparison.Ordinal) ||
        path.Split('/', StringSplitOptions.RemoveEmptyEntries).Any(segment => segment is "." or ".."))
      throw new ArgumentException("A bounded absolute Polar path is required.", nameof(path));
  }

  private sealed record SimulatedRecording(string Identifier, long SizeBytes, byte[] Payload);
  private int _activeSampleType;
  private int _activeIntervalSeconds;
}
