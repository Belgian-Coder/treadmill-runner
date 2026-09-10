using System.Text;
using TreadmillRunner.Protocols.Polar;

namespace TreadmillRunner.Protocols.Tests;

public sealed class PolarPftpClientTests
{
  [Fact]
  public async Task Uses_published_query_ids_and_start_parameter_layout()
  {
    var connection = new FakeConnection(_ => ReadOnlyMemory<byte>.Empty);
    var client = new PolarPftpClient(connection);

    await client.StartAsync("run-1", PolarRecordingSampleType.HeartRate, 1);
    await client.StopAsync();

    Assert.Equal(new byte[] { 14, 0x80, 8, 1, 18, 2, 24, 1, 26, 5, (byte)'r', (byte)'u', (byte)'n', (byte)'-', (byte)'1' }, connection.Requests[0]);
    Assert.Equal(new byte[] { 15, 0x80 }, connection.Requests[1]);
  }

  [Fact]
  public async Task Parses_recording_status_and_requires_its_flag()
  {
    var connection = new FakeConnection(_ => new byte[] { 8, 1, 18, 5, (byte)'r', (byte)'u', (byte)'n', (byte)'-', (byte)'1' });
    PolarRecordingStatus status = await new PolarPftpClient(connection).GetStatusAsync();
    Assert.True(status.IsRecording);
    Assert.Equal("run-1", status.EntryId);
    Assert.Equal(new byte[] { 16, 0x80 }, connection.Requests.Single());

    await Assert.ThrowsAsync<FormatException>(async () =>
      await new PolarPftpClient(new FakeConnection(_ => new byte[] { 18, 1, (byte)'x' })).GetStatusAsync());
  }

  [Fact]
  public async Task Recursively_lists_only_samples_files_with_exact_paths()
  {
    var connection = new FakeConnection(request => DecodePath(request.Span) switch
    {
      "/" => Directory(("run-a/", 0), ("ignored.txt", 3)),
      "/run-a/" => Directory(("SAMPLES.BPB", 27)),
      _ => throw new InvalidOperationException(),
    });

    PolarExerciseSummary recording = Assert.Single(await new PolarPftpClient(connection).ListExercisesAsync());
    Assert.Equal("/run-a/SAMPLES.BPB", recording.Identifier);
    Assert.Equal(27, recording.SizeBytes);
    Assert.Equal(2, connection.Requests.Count);
  }

  [Fact]
  public async Task Parses_packed_hr_and_rr_exercise_payloads()
  {
    byte[] duration = PolarPftpProtobuf.EncodeBytesField(1, PolarPftpProtobuf.EncodeFields((3, 1)));
    byte[] heartRates = duration.Concat(PolarPftpProtobuf.EncodeBytesField(2, [100, 101, 102])).ToArray();
    PolarExerciseSamples hr = await new PolarPftpClient(new FakeConnection(_ => heartRates)).FetchExerciseAsync("/hr/SAMPLES.BPB");
    Assert.Equal(PolarRecordingSampleType.HeartRate, hr.SampleType);
    Assert.Equal(new ushort[] { 100, 101, 102 }, hr.HeartRateSamples);

    byte[] rrMessage = duration.Concat(PolarPftpProtobuf.EncodeBytesField(28,
      PolarPftpProtobuf.EncodeBytesField(1, [0xe8, 0x07, 0xd0, 0x07]))).ToArray();
    PolarExerciseSamples rr = await new PolarPftpClient(new FakeConnection(_ => rrMessage)).FetchExerciseAsync("/rr/SAMPLES.BPB");
    Assert.Equal(PolarRecordingSampleType.RrInterval, rr.SampleType);
    Assert.Equal(new uint[] { 1000, 976 }, rr.RrIntervalsMilliseconds);
  }

  [Fact]
  public void Rejects_out_of_sync_continuation_and_remote_errors()
  {
    Assert.Throws<FormatException>(() => PolarPftpFrameCodec.Reassemble([
      new byte[] { 0x07, 1 },
      new byte[] { 0x13, 2 },
    ]));
    PolarPftpProtocolException error = Assert.Throws<PolarPftpProtocolException>(() =>
      PolarPftpFrameCodec.Reassemble([new byte[] { 0x00, 103, 0 }]));
    Assert.Equal((ushort)103, error.Code);
  }

  private static byte[] Directory(params (string Name, ulong Size)[] entries) => entries
    .SelectMany(entry => PolarPftpProtobuf.EncodeBytesField(1,
      PolarPftpProtobuf.EncodeBytesField(1, Encoding.UTF8.GetBytes(entry.Name))
        .Concat(PolarPftpProtobuf.EncodeFields((2, entry.Size))).ToArray()))
    .ToArray();

  private static string DecodePath(ReadOnlySpan<byte> request)
  {
    (ReadOnlyMemory<byte> operation, _) = PolarPftpRfc60Codec.DecodeOperation(request.ToArray());
    PolarProtobufField path = PolarPftpProtobuf.DecodeFields(operation.Span).Single(field => field.Number == 2);
    return Encoding.UTF8.GetString(path.Bytes.Span);
  }

  private sealed class FakeConnection(Func<ReadOnlyMemory<byte>, ReadOnlyMemory<byte>> exchange) : IPolarPftpConnection
  {
    public string DeviceId => "000000000000";
    public List<byte[]> Requests { get; } = [];

    public ValueTask<ReadOnlyMemory<byte>> ExchangeAsync(ReadOnlyMemory<byte> request, TimeSpan responseTimeout, int maximumResponseBytes, CancellationToken cancellationToken = default)
    {
      Requests.Add(request.ToArray());
      ReadOnlyMemory<byte> response = exchange(request);
      if (response.Length > maximumResponseBytes) throw new InvalidOperationException();
      return ValueTask.FromResult(response);
    }

    public ValueTask DisposeAsync() => ValueTask.CompletedTask;
  }
}
