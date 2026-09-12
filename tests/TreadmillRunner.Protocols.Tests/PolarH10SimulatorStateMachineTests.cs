using System.Text;
using TreadmillRunner.PolarH10Simulator;
using TreadmillRunner.Protocols.Polar;

namespace TreadmillRunner.Protocols.Tests;

public sealed class PolarH10SimulatorStateMachineTests
{
  [Fact]
  public async Task Production_client_completes_the_memory_lifecycle_against_the_independent_simulator()
  {
    var state = new PolarH10SimulatorStateMachine(new PolarH10SimulatorOptions
    {
      IncludeSeedRecording = false,
      HeartRateSamples = [101, 102, 103],
    });
    state.BeginConnection();
    await using var connection = new SimulatorConnection(state);
    var client = new PolarPftpClient(connection, TimeSpan.FromSeconds(1));

    Assert.False((await client.GetStatusAsync()).IsRecording);
    await client.StartAsync("production-run");
    PolarRecordingStatus recording = await client.GetStatusAsync();
    Assert.True(recording.IsRecording);
    Assert.Equal("production-run", recording.EntryId);
    await client.StopAsync();

    PolarExerciseSummary summary = Assert.Single(await client.ListExercisesAsync());
    Assert.Equal("/production-run/SAMPLES.BPB", summary.Identifier);
    PolarExerciseSamples samples = await client.FetchExerciseAsync(summary.Identifier);
    Assert.Equal(new ushort[] { 101, 102, 103 }, samples.HeartRateSamples);
    await client.RemoveExerciseAsync(summary.Identifier);
    Assert.Empty(await client.ListExercisesAsync());
  }

  [Fact]
  public void Publishes_the_public_heart_rate_and_pftp_service_contract()
  {
    Assert.Equal(Guid.Parse("0000180d-0000-1000-8000-00805f9b34fb"), PolarH10SimulatorUuids.HeartRateService);
    Assert.Equal(Guid.Parse("00002a37-0000-1000-8000-00805f9b34fb"), PolarH10SimulatorUuids.HeartRateMeasurement);
    Assert.Equal(Guid.Parse("0000180f-0000-1000-8000-00805f9b34fb"), PolarH10SimulatorUuids.BatteryService);
    Assert.Equal(Guid.Parse("00002a19-0000-1000-8000-00805f9b34fb"), PolarH10SimulatorUuids.BatteryLevel);
    Assert.Equal(Guid.Parse("0000feee-0000-1000-8000-00805f9b34fb"), PolarH10SimulatorUuids.PolarPftpService);
    Assert.Equal(Guid.Parse("fb005c51-02e7-f387-1cad-8acd2d8df0c8"), PolarH10SimulatorUuids.PolarPftpMtu);
    Assert.Equal(Guid.Parse("fb005c52-02e7-f387-1cad-8acd2d8df0c8"), PolarH10SimulatorUuids.PolarPftpDeviceToHost);
    Assert.Equal(Guid.Parse("fb005c53-02e7-f387-1cad-8acd2d8df0c8"), PolarH10SimulatorUuids.PolarPftpHostToDevice);
  }

  [Fact]
  public void Handles_status_start_stop_list_read_and_remove_without_production_services()
  {
    var state = new PolarH10SimulatorStateMachine(new PolarH10SimulatorOptions
    {
      SeedExerciseId = "seed",
      HeartRateSamples = [101, 102, 103],
    });
    state.BeginConnection();

    Assert.False(ReadStatus(Exchange(state, PolarH10SimulatorFrameCodec.EncodeQuery(16))).IsRecording);

    byte[] startParameters = PolarH10SimulatorProtobuf.EncodeFields((1, 1))
      .Concat(PolarH10SimulatorProtobuf.EncodeBytesField(2, PolarH10SimulatorProtobuf.EncodeFields((3, 1))))
      .Concat(PolarH10SimulatorProtobuf.EncodeBytesField(3, "run-1"u8)).ToArray();
    Assert.Empty(Exchange(state, PolarH10SimulatorFrameCodec.EncodeQuery(14, startParameters)));
    PolarH10SimulatorRecordingStatus recordingStatus = ReadStatus(Exchange(state, PolarH10SimulatorFrameCodec.EncodeQuery(16)));
    Assert.True(recordingStatus.IsRecording);
    Assert.Equal("run-1", recordingStatus.EntryId);

    Assert.Empty(Exchange(state, PolarH10SimulatorFrameCodec.EncodeQuery(15)));
    byte[] directory = Exchange(state, Operation(0, "/"));
    Assert.Contains("seed/", ReadDirectoryNames(directory));
    Assert.Contains("run-1/", ReadDirectoryNames(directory));
    Assert.Contains("SAMPLES.BPB", ReadDirectoryNames(Exchange(state, Operation(0, "/run-1/"))));

    byte[] samples = Exchange(state, Operation(0, "/run-1/SAMPLES.BPB"));
    Assert.Equal(new ulong[] { 101, 102, 103 }, ReadPackedField(samples, 2));
    Assert.Empty(Exchange(state, Operation(3, "/run-1/SAMPLES.BPB")));
    Assert.DoesNotContain("run-1/", ReadDirectoryNames(Exchange(state, Operation(0, "/"))));
  }

  [Fact]
  public void Fragments_requests_and_deterministically_marks_drop_and_disconnect_faults()
  {
    var state = new PolarH10SimulatorStateMachine(new PolarH10SimulatorOptions
    {
      FrameSize = 4,
      DropEveryNthNotification = 2,
      DisconnectAfterNotifications = 3,
    });
    state.BeginConnection();
    byte sequence = 0;
    IReadOnlyList<byte[]> packets = PolarH10SimulatorFrameCodec.EncodeRequest(
      Operation(3, "/simulated-run/SAMPLES.BPB"), 4, ref sequence);

    Assert.All(packets.Take(packets.Count - 1), packet => Assert.Empty(state.AcceptHostPacket(packet)));
    IReadOnlyList<PolarH10SimulatorEmission> first = state.AcceptHostPacket(packets[^1]);
    Assert.Single(first);
    Assert.Equal(1, first[0].AttemptNumber);
    Assert.False(first[0].Dropped);
    Assert.False(first[0].DisconnectRequested);

    sequence = 0;
    IReadOnlyList<PolarH10SimulatorEmission> second = state.AcceptHostPacket(
      PolarH10SimulatorFrameCodec.EncodeRequest(PolarH10SimulatorFrameCodec.EncodeQuery(16), 4, ref sequence)[^1]);
    Assert.True(second[0].Dropped);
    Assert.False(second[0].DisconnectRequested);

    sequence = 0;
    IReadOnlyList<PolarH10SimulatorEmission> third = state.AcceptHostPacket(
      PolarH10SimulatorFrameCodec.EncodeRequest(PolarH10SimulatorFrameCodec.EncodeQuery(16), 4, ref sequence)[^1]);
    Assert.True(third[0].DisconnectRequested);
    Assert.True(state.DisconnectRequested);
    Assert.Empty(state.AcceptHostPacket(packets[^1]));
  }

  [Fact]
  public void Emits_standard_heart_rate_measurements_and_rejects_unsafe_paths()
  {
    var state = new PolarH10SimulatorStateMachine(new PolarH10SimulatorOptions
    {
      HeartRateSamples = [72, 180],
    });
    state.BeginConnection();
    PolarH10SimulatorEmission first = state.CreateHeartRateNotification();
    PolarH10SimulatorEmission second = state.CreateHeartRateNotification();
    Assert.Equal(new byte[] { 0, 72 }, first.Value);
    Assert.Equal(new byte[] { 0, 180 }, second.Value);

    byte sequence = 0;
    byte[] unsafePath = PolarH10SimulatorFrameCodec.EncodeOperation(
      PolarH10SimulatorProtobuf.EncodeFields((1, 0))
        .Concat(PolarH10SimulatorProtobuf.EncodeBytesField(2, "/../SAMPLES.BPB"u8)).ToArray());
    IReadOnlyList<PolarH10SimulatorEmission> error = PolarH10SimulatorFrameCodec
      .EncodeRequest(unsafePath, PolarH10SimulatorFrameCodec.DefaultFrameSize, ref sequence)
      .SelectMany(packet => state.AcceptHostPacket(packet)).ToArray();
    Assert.Equal(PolarH10SimulatorFrameStatus.ResponseOrError, PolarH10SimulatorFrameCodec.Decode(error.Single().Value).Status);
  }

  [Fact]
  public void Produces_bounded_rr_samples_for_the_rr_start_mode()
  {
    var state = new PolarH10SimulatorStateMachine(new PolarH10SimulatorOptions
    {
      IncludeSeedRecording = false,
      RrIntervalsMilliseconds = [1000, 980, 1020],
    });
    state.BeginConnection();
    byte[] interval = PolarH10SimulatorProtobuf.EncodeFields((3, 5));
    byte[] parameters = PolarH10SimulatorProtobuf.EncodeFields((1, 16))
      .Concat(PolarH10SimulatorProtobuf.EncodeBytesField(2, interval))
      .Concat(PolarH10SimulatorProtobuf.EncodeBytesField(3, "rr-run"u8)).ToArray();
    Assert.Empty(Exchange(state, PolarH10SimulatorFrameCodec.EncodeQuery(14, parameters)));
    Assert.Empty(Exchange(state, PolarH10SimulatorFrameCodec.EncodeQuery(15)));
    byte[] samples = Exchange(state, Operation(0, "/rr-run/SAMPLES.BPB"));
    PolarH10SimulatorProtobufField rrOuter = PolarH10SimulatorProtobuf.DecodeFields(samples)
      .Single(field => field.Number == 28);
    PolarH10SimulatorProtobufField rrValues = PolarH10SimulatorProtobuf.DecodeFields(rrOuter.Bytes.Span)
      .Single(field => field.Number == 1);
    int offset = 0;
    var values = new List<ulong>();
    while (offset < rrValues.Bytes.Length) values.Add(PolarH10SimulatorProtobuf.ReadVarint(rrValues.Bytes.Span, ref offset));
    Assert.Equal(new ulong[] { 1000, 980, 1020 }, values);
  }

  private static byte[] Exchange(PolarH10SimulatorStateMachine state, byte[] message)
  {
    byte sequence = 0;
    IReadOnlyList<PolarH10SimulatorEmission> emissions = PolarH10SimulatorFrameCodec
      .EncodeRequest(message, state.Options.FrameSize, ref sequence)
      .SelectMany(packet => state.AcceptHostPacket(packet))
      .ToArray();
    Assert.NotEmpty(emissions);
    Assert.DoesNotContain(emissions, static emission => emission.Dropped);
    return PolarH10SimulatorFrameCodec.Reassemble(emissions.Select(static emission => (ReadOnlyMemory<byte>)emission.Value));
  }

  private static byte[] Operation(int command, string path)
  {
    byte[] operation = PolarH10SimulatorProtobuf.EncodeFields((1, (ulong)command))
      .Concat(PolarH10SimulatorProtobuf.EncodeBytesField(2, Encoding.UTF8.GetBytes(path))).ToArray();
    return PolarH10SimulatorFrameCodec.EncodeOperation(operation);
  }

  private static PolarH10SimulatorRecordingStatus ReadStatus(byte[] payload)
  {
    bool? isRecording = null;
    string? entryId = null;
    foreach (PolarH10SimulatorProtobufField field in PolarH10SimulatorProtobuf.DecodeFields(payload))
    {
      if (field.Number == 1 && field.WireType == 0) isRecording = field.Varint != 0;
      if (field.Number == 2 && field.WireType == 2) entryId = Encoding.UTF8.GetString(field.Bytes.Span);
    }
    return new(isRecording ?? throw new FormatException(), entryId);
  }

  private static IReadOnlyList<string> ReadDirectoryNames(byte[] payload) =>
    PolarH10SimulatorProtobuf.DecodeFields(payload).Where(static field => field.Number == 1 && field.WireType == 2)
      .Select(static field => Encoding.UTF8.GetString(
        PolarH10SimulatorProtobuf.DecodeFields(field.Bytes.Span).Single(inner => inner.Number == 1).Bytes.Span))
      .ToArray();

  private static IReadOnlyList<ulong> ReadPackedField(byte[] payload, int number)
  {
    PolarH10SimulatorProtobufField field = PolarH10SimulatorProtobuf.DecodeFields(payload)
      .Single(candidate => candidate.Number == number && candidate.WireType == 2);
    int offset = 0;
    var values = new List<ulong>();
    while (offset < field.Bytes.Length) values.Add(PolarH10SimulatorProtobuf.ReadVarint(field.Bytes.Span, ref offset));
    return values;
  }

  private sealed record PolarH10SimulatorRecordingStatus(bool IsRecording, string? EntryId);

  private sealed class SimulatorConnection(PolarH10SimulatorStateMachine state) : IPolarPftpConnection
  {
    public string DeviceId => "SYNTHETIC";

    public ValueTask<ReadOnlyMemory<byte>> ExchangeAsync(
      ReadOnlyMemory<byte> request,
      TimeSpan responseTimeout,
      int maximumResponseBytes,
      CancellationToken cancellationToken = default)
    {
      cancellationToken.ThrowIfCancellationRequested();
      byte sequence = 0;
      IReadOnlyList<PolarH10SimulatorEmission> emissions = PolarPftpFrameCodec
        .EncodeRequest(request.Span, state.Options.FrameSize, ref sequence)
        .SelectMany(packet => state.AcceptHostPacket(packet))
        .ToArray();
      if (emissions.Count == 0 || emissions.Any(static emission => emission.Dropped))
        return ValueTask.FromException<ReadOnlyMemory<byte>>(new TimeoutException("Synthetic notification drop."));
      ReadOnlyMemory<byte> response = PolarPftpFrameCodec.Reassemble(
        emissions.Select(static emission => (ReadOnlyMemory<byte>)emission.Value),
        expectedFirstSequence: 0,
        maximumBytes: maximumResponseBytes);
      return ValueTask.FromResult(response);
    }

    public ValueTask DisposeAsync() => ValueTask.CompletedTask;
  }
}
