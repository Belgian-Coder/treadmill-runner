using System.Security.Cryptography;
using System.Text.Json;

namespace TreadmillRunner.Gateway.Planning;

internal static class PlanningOperationFingerprint
{
  private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

  public static string Compute<TScope>(TScope scope)
  {
    byte[] requestBytes = JsonSerializer.SerializeToUtf8Bytes(scope, JsonOptions);
    return Convert.ToHexStringLower(SHA256.HashData(requestBytes));
  }
}
