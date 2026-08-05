namespace TreadmillRunner.Protocols.Omega;

public enum OmegaFrameDiagnosticCode
{
  TruncatedFrame,
}

public sealed record OmegaFrameDiagnostic(
    OmegaFrameDiagnosticCode Code,
    int? ExpectedLength,
    int ReceivedLength);

public sealed class OmegaFrameReassembler
{
  private readonly List<byte> _buffer = [];
  private readonly List<OmegaFrameDiagnostic> _diagnostics = [];
  private int? _expectedLength;

  public IReadOnlyList<OmegaFrameDiagnostic> Diagnostics => _diagnostics.AsReadOnly();

  public IReadOnlyList<byte[]> Append(ReadOnlySpan<byte> fragment)
  {
    var frames = new List<byte[]>();

    foreach (var value in fragment)
    {
      AppendByte(value, frames);
    }

    return frames;
  }

  public void Reset(bool reportTruncatedFrame = true)
  {
    if (reportTruncatedFrame && _buffer.Count > 0)
    {
      ReportTruncated(_buffer.Count);
    }

    ClearCandidate();
  }

  public void ClearDiagnostics() => _diagnostics.Clear();

  private void AppendByte(byte value, List<byte[]> frames)
  {
    if (_buffer.Count == 0)
    {
      if (value == 0x55)
      {
        _buffer.Add(value);
      }

      return;
    }

    if (_buffer.Count == 1)
    {
      if (value == 0xAA)
      {
        _buffer.Add(value);
      }
      else if (value != 0x55)
      {
        ClearCandidate();
      }

      return;
    }

    if (_buffer[^1] == 0x55 && value == 0xAA)
    {
      ReportTruncated(_buffer.Count - 1);
      _buffer.Clear();
      _buffer.Add(0x55);
      _buffer.Add(0xAA);
      _expectedLength = null;
      return;
    }

    _buffer.Add(value);

    if (_buffer.Count == 8)
    {
      var payloadLength = _buffer[6] | (_buffer[7] << 8);
      _expectedLength = payloadLength + 10;
    }

    if (_expectedLength is not null && _buffer.Count == _expectedLength)
    {
      frames.Add(_buffer.ToArray());
      ClearCandidate();
    }
  }

  private void ReportTruncated(int receivedLength) => _diagnostics.Add(
      new OmegaFrameDiagnostic(
          OmegaFrameDiagnosticCode.TruncatedFrame,
          _expectedLength,
          receivedLength));

  private void ClearCandidate()
  {
    _buffer.Clear();
    _expectedLength = null;
  }
}
