using System.Globalization;
using System.Text;
using Dynastream.Fit;

namespace TreadmillRunner.Protocols.Exports;

/// <summary>
/// Compares two Garmin FIT Activity files by their decoded activity content.
///
/// FIT headers, container framing and CRCs are validated but are not part of
/// the comparison.  Device/developer metadata and FileId identity are also
/// deliberately excluded because Garmin may rewrite those while retaining the
/// same activity.  All remaining ordinary fields are compared exactly in
/// decoded, wire-quantized form and in message order.
/// </summary>
public static class GarminFitActivitySemantics
{
  private const int MaximumFitBytes = 16 * 1024 * 1024;

  /// <summary>
  /// Returns <see langword="true"/> when both files are valid FIT Activity
  /// files with the same ordinary decoded message content.
  /// </summary>
  /// <remarks>
  /// Malformed, incomplete, unsupported or ambiguous files return
  /// <see langword="false"/>.  The method never throws for untrusted bytes.
  /// </remarks>
  public static bool AreEquivalent(byte[] expected, byte[] candidate)
  {
    if (!TryCanonicalize(expected, out string? expectedCanonical) ||
        !TryCanonicalize(candidate, out string? candidateCanonical))
    {
      return false;
    }

    return string.Equals(expectedCanonical, candidateCanonical, StringComparison.Ordinal);
  }

  private static bool TryCanonicalize(byte[]? bytes, out string? canonical)
  {
    canonical = null;
    if (bytes is null || bytes.Length is 0 or > MaximumFitBytes)
    {
      return false;
    }

    try
    {
      using var stream = new MemoryStream(bytes, writable: false);
      var decoder = new Decode();
      if (!decoder.IsFIT(stream))
      {
        return false;
      }

      stream.Position = 0;
      if (!decoder.CheckIntegrity(stream))
      {
        return false;
      }

      stream.Position = 0;
      var messages = new List<Mesg>();
      decoder.MesgEvent += (_, args) => messages.Add(new Mesg(args.mesg));
      if (!decoder.Read(stream) || messages.Count == 0)
      {
        return false;
      }

      // FileId is the only identity-bearing message that is required to
      // establish the Activity file contract.  Multiple FileIds are
      // ambiguous and therefore fail closed.
      List<Mesg> fileIds = messages.Where(static message => message.Num == (ushort)MesgNum.FileId).ToList();
      if (fileIds.Count != 1)
      {
        return false;
      }

      var fileId = new FileIdMesg(fileIds[0]);
      if (fileId.GetType() != Dynastream.Fit.File.Activity)
      {
        return false;
      }

      // Metadata-only messages are explicitly outside activity semantics.
      // Developer fields are never present in Mesg.Fields and consequently
      // are ignored by the generic field normalization below.
      List<Mesg> coreMessages = messages
        .Where(static message => !IsIgnoredMessage(message.Num))
        .ToList();
      if (coreMessages.Count(message => message.Num == (ushort)MesgNum.Record) < 1 ||
          coreMessages.Count(message => message.Num == (ushort)MesgNum.Session) < 1 ||
          coreMessages.Count(message => message.Num == (ushort)MesgNum.Activity) < 1)
      {
        return false;
      }

      var builder = new StringBuilder(coreMessages.Count * 128);
      foreach (Mesg message in coreMessages)
      {
        AppendMessage(builder, message);
      }

      canonical = builder.ToString();
      return true;
    }
    catch (Exception)
    {
      // FIT SDK decoding can throw several exception types depending on where
      // malformed bytes are encountered.  This API is used on downloaded,
      // untrusted files, so all such failures are non-matches.
      canonical = null;
      return false;
    }
  }

  private static bool IsIgnoredMessage(ushort messageNum) => messageNum is
      (ushort)MesgNum.DeviceInfo or
      (ushort)MesgNum.DeveloperDataId or
      (ushort)MesgNum.FieldDescription;

  private static void AppendMessage(StringBuilder builder, Mesg message)
  {
    builder.Append("M:").Append(message.Num).Append(';');

    IEnumerable<Field> fields = message.Fields
      .Where(field => !IsIgnoredField(message.Num, field))
      .OrderBy(field => field.Num);
    foreach (Field field in fields)
    {
      builder.Append("F:").Append(field.Num).Append(':').Append(field.GetNumValues()).Append('[');
      for (var index = 0; index < field.GetNumValues(); index++)
      {
        builder.Append(ValueToken(field.GetRawValue(index)));
      }

      builder.Append(']');
    }

    builder.Append('|');
  }

  private static bool IsIgnoredField(ushort messageNum, Field field)
  {
    // compressed_speed_distance is a rolling/compressed representation of
    // distance/speed and is expected to be rewritten by Garmin.  Compare the
    // ordinary speed and distance fields instead.
    if (messageNum == (ushort)MesgNum.Record && field.Num == 8)
    {
      return true;
    }

    // FileId type is the semantic contract.  Manufacturer/product/serial,
    // creation timestamp, product name and number are identity metadata that
    // Garmin legitimately changes when an activity is copied or re-uploaded.
    return messageNum == (ushort)MesgNum.FileId && field.Num != 0;
  }

  private static string ValueToken(object? value)
  {
    if (value is null)
    {
      return "N;";
    }

    if (value is byte[] bytes)
    {
      return $"B:{bytes.Length}:{Convert.ToHexString(bytes)};";
    }

    string typeName = value.GetType().FullName ?? value.GetType().Name;
    string text = value is IFormattable formattable
      ? formattable.ToString(null, CultureInfo.InvariantCulture) ?? string.Empty
      : value.ToString() ?? string.Empty;
    return $"V:{typeName.Length}:{typeName}:{text.Length}:{text};";
  }
}
