namespace TreadmillRunner.Protocols.Omega;

public enum OmegaFrameDiagnosticCode
{
  TruncatedFrame,
  LengthOutOfRange,
  InvalidTerminator,
}

public sealed record OmegaFrameDiagnostic(
    OmegaFrameDiagnosticCode Code,
    int? ExpectedLength,
    int ReceivedLength);

public sealed class OmegaFrameReassembler
{
  // The status stream has no negotiated maximum. Keep a generous hard bound
  // so a corrupt declared length cannot retain unbounded bytes in memory.
  internal const int MaximumPayloadLength = 4096;
  private const int HeaderLength = 10;
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

    // Before the declared length is known a new header is the only safe
    // resynchronization signal. Once known, payload bytes are opaque and may
    // legitimately contain 0x55,0xAA; honor the declared boundary.
    if (_expectedLength is null && _buffer[^1] == 0x55 && value == 0xAA)
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
      if (payloadLength > MaximumPayloadLength)
      {
        _diagnostics.Add(new OmegaFrameDiagnostic(
          OmegaFrameDiagnosticCode.LengthOutOfRange,
          payloadLength + HeaderLength,
          _buffer.Count));
        ClearCandidate();
        return;
      }

      _expectedLength = payloadLength + HeaderLength;
    }

    if (_expectedLength is not null && _buffer.Count == _expectedLength)
    {
      bool retainHeader = false;
      bool retainHeaderStart = false;
      if (_buffer[^2] != 0x0D || _buffer[^1] != 0x0A)
      {
        _diagnostics.Add(new OmegaFrameDiagnostic(
          OmegaFrameDiagnosticCode.InvalidTerminator,
          _expectedLength,
          _buffer.Count));
        // A corrupt declared length can make the next frame's header occupy
        // the expected terminator bytes. Preserve that unambiguous suffix so
        // the following frame is not discarded while resynchronizing.
        retainHeader = _buffer[^2] == 0x55 && _buffer[^1] == 0xAA;
        retainHeaderStart = !retainHeader && _buffer[^1] == 0x55;
      }
      else
      {
        frames.Add(_buffer.ToArray());
      }
      ClearCandidate();
      if (retainHeader)
      {
        _buffer.Add(0x55);
        _buffer.Add(0xAA);
      }
      else if (retainHeaderStart)
      {
        _buffer.Add(0x55);
      }
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
