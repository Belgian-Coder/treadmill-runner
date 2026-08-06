using System.Reflection;

namespace TreadmillRunner.Core.System;

public static class AppBuildInfo
{
  public static string ReleaseVersion { get; } =
    typeof(AppBuildInfo).Assembly.GetName().Version?.ToString(3) ?? "0.0.0";

  public static string Fingerprint { get; } = CreateFingerprint();

  private static string CreateFingerprint()
  {
    Assembly assembly = typeof(AppBuildInfo).Assembly;
    string value = assembly.GetCustomAttributes<AssemblyMetadataAttribute>()
      .SingleOrDefault(attribute => attribute.Key == "TreadmillRunnerBuildId")?.Value
      ?? assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion
      ?? ReleaseVersion;
    return Convert.ToHexString(global::System.Security.Cryptography.SHA256.HashData(
      global::System.Text.Encoding.UTF8.GetBytes(value)))[..16].ToLowerInvariant();
  }
}
