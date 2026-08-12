using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using Net.Codecrete.QrCodeGenerator;

namespace TreadmillRunner.Gateway.Operations;

public sealed record AppAccessCandidate(
  string Id,
  string Label,
  string Url,
  bool IsSecure);

public sealed record AppAccessView(
  bool Available,
  string? PreferredCandidateId,
  IReadOnlyList<AppAccessCandidate> Candidates,
  string Message);

public static class GatewayListenerConfiguration
{
  public static bool HasStructuredKestrelEndpoints(IConfiguration configuration) =>
    configuration.GetSection("Kestrel:Endpoints").GetChildren().Any(endpoint =>
      !string.IsNullOrWhiteSpace(endpoint["Url"]));

  public static IReadOnlyList<string> GetListenUrls(IConfiguration configuration)
  {
    string[] structured = configuration.GetSection("Kestrel:Endpoints")
      .GetChildren()
      .Select(endpoint => endpoint["Url"])
      .Where(static url => !string.IsNullOrWhiteSpace(url))
      .Select(static url => url!)
      .ToArray();

    if (structured.Length > 0)
    {
      return structured;
    }

    string? configured = configuration["Gateway:Urls"] ?? configuration["ASPNETCORE_URLS"];
    return (configured ?? string.Empty)
      .Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
  }
}

public sealed class AppAccessUrlService(IConfiguration configuration)
{
  private const int MaximumUrlLength = 2_048;

  public AppAccessView GetView()
  {
    IReadOnlyList<AppAccessCandidate> candidates = ResolveCandidates();
    return candidates.Count == 0
      ? new AppAccessView(
        false,
        null,
        candidates,
        "No private-LAN address is available. Configure Gateway:PublicUrl or connect the server to the household network.")
      : new AppAccessView(
        true,
        candidates[0].Id,
        candidates,
        "Scan from a device on the same private Wi-Fi network.");
  }

  public AppAccessCandidate? Find(string id) => ResolveCandidates()
    .FirstOrDefault(candidate => string.Equals(candidate.Id, id, StringComparison.Ordinal));

  internal IReadOnlyList<AppAccessCandidate> ResolveCandidates()
  {
    var candidates = new List<AppAccessCandidate>();
    if (TryNormalizeConfiguredUrl(configuration["Gateway:PublicUrl"], configuration, out Uri configured))
    {
      AddCandidate(candidates, configured, "Configured address");
    }

    foreach (string text in GatewayListenerConfiguration.GetListenUrls(configuration))
    {
      if (!Uri.TryCreate(text, UriKind.Absolute, out Uri? listener) ||
          listener.Scheme is not ("http" or "https"))
      {
        continue;
      }

      if (IsWildcardHost(listener.Host))
      {
        foreach (IPAddress address in PrivateIpv4Addresses())
        {
          AddCandidate(candidates, BuildUrl(listener, address.ToString()), $"Private Wi-Fi · {address}");
        }
        continue;
      }

      if (IPAddress.TryParse(listener.Host, out IPAddress? parsed))
      {
        if (IsPrivateIpv4(parsed))
        {
          AddCandidate(candidates, BuildUrl(listener, parsed.ToString()), $"Private Wi-Fi · {parsed}");
        }
        continue;
      }

      if (IsLocalHostname(listener.Host))
      {
        AddCandidate(candidates, BuildUrl(listener, listener.Host), $"Server name · {listener.Host}");
      }
    }

    return candidates
      .DistinctBy(static candidate => candidate.Url, StringComparer.OrdinalIgnoreCase)
      .ToArray();
  }

  private static bool TryNormalizeConfiguredUrl(string? value, IConfiguration configuration, out Uri normalized)
  {
    normalized = null!;
    if (string.IsNullOrWhiteSpace(value) || value.Length > MaximumUrlLength ||
        !Uri.TryCreate(value.Trim(), UriKind.Absolute, out Uri? parsed) ||
        parsed.Scheme is not ("http" or "https") ||
        !string.IsNullOrEmpty(parsed.UserInfo) ||
        !string.IsNullOrEmpty(parsed.Query) ||
        !string.IsNullOrEmpty(parsed.Fragment) ||
        IsWildcardHost(parsed.Host) ||
        string.Equals(parsed.Host, "localhost", StringComparison.OrdinalIgnoreCase) ||
        !IsLocalAddress(parsed.Host, configuration))
    {
      return false;
    }

    normalized = new UriBuilder(parsed) { Path = EnsureTrailingSlash(parsed.AbsolutePath) }.Uri;
    return true;
  }

  private static Uri BuildUrl(Uri listener, string host) => new UriBuilder(listener.Scheme, host, listener.Port, "/").Uri;

  private static string EnsureTrailingSlash(string path) => path.EndsWith("/", StringComparison.Ordinal) ? path : $"{path}/";

  private static bool IsWildcardHost(string host) => host is "0.0.0.0" or "::" or "[::]" or "*" or "+";

  private static bool IsLocalAddress(string host, IConfiguration configuration) => IPAddress.TryParse(host, out IPAddress? address)
    ? IsPrivateIpv4(address)
    : IsLocalHostname(host) || IsConfiguredPrivateSuffix(host, configuration);

  private static bool IsConfiguredPrivateSuffix(string host, IConfiguration configuration) =>
    configuration.GetSection("Gateway:AllowedPublicHostSuffixes").Get<string[]>() is { Length: > 0 } suffixes &&
    suffixes.Any(suffix =>
      !string.IsNullOrWhiteSpace(suffix) &&
      host.EndsWith($".{suffix.Trim().TrimStart('.')}", StringComparison.OrdinalIgnoreCase));

  private static bool IsLocalHostname(string host) =>
    !string.IsNullOrWhiteSpace(host) &&
    (!host.Contains('.', StringComparison.Ordinal) ||
     host.EndsWith(".local", StringComparison.OrdinalIgnoreCase) ||
     host.EndsWith(".home", StringComparison.OrdinalIgnoreCase) ||
     host.EndsWith(".lan", StringComparison.OrdinalIgnoreCase) ||
     host.EndsWith(".internal", StringComparison.OrdinalIgnoreCase));

  private static IEnumerable<IPAddress> PrivateIpv4Addresses()
  {
    return NetworkInterface.GetAllNetworkInterfaces()
      .Where(static adapter => adapter.OperationalStatus == OperationalStatus.Up &&
        adapter.NetworkInterfaceType is not (NetworkInterfaceType.Loopback or NetworkInterfaceType.Tunnel))
      .OrderByDescending(static adapter => adapter.GetIPProperties().GatewayAddresses.Any(gateway =>
        gateway.Address.AddressFamily == AddressFamily.InterNetwork &&
        !gateway.Address.Equals(IPAddress.Any)))
      .ThenBy(static adapter => AdapterPriority(adapter.NetworkInterfaceType))
      .ThenBy(static adapter => adapter.Name, StringComparer.OrdinalIgnoreCase)
      .SelectMany(static adapter => adapter.GetIPProperties().UnicastAddresses)
      .Select(static address => address.Address)
      .Where(IsPrivateIpv4)
      .OrderBy(static address => address.ToString(), StringComparer.Ordinal);
  }

  private static int AdapterPriority(NetworkInterfaceType type) => type switch
  {
    NetworkInterfaceType.Ethernet => 0,
    NetworkInterfaceType.Wireless80211 => 1,
    _ => 2,
  };

  private static bool IsPrivateIpv4(IPAddress address)
  {
    if (address.AddressFamily != AddressFamily.InterNetwork || IPAddress.IsLoopback(address))
    {
      return false;
    }

    byte[] bytes = address.GetAddressBytes();
    return bytes[0] == 10 ||
      (bytes[0] == 172 && bytes[1] is >= 16 and <= 31) ||
      (bytes[0] == 192 && bytes[1] == 168);
  }

  private static void AddCandidate(List<AppAccessCandidate> candidates, Uri url, string label)
  {
    string value = url.AbsoluteUri;
    if (value.Length > MaximumUrlLength)
    {
      return;
    }

    byte[] digest = SHA256.HashData(Encoding.UTF8.GetBytes(value));
    candidates.Add(new AppAccessCandidate(
      Convert.ToHexStringLower(digest.AsSpan(0, 8)),
      label,
      value,
      string.Equals(url.Scheme, Uri.UriSchemeHttps, StringComparison.Ordinal)));
  }
}

public static class AppAccessEndpoints
{
  public static RouteGroupBuilder MapAppAccess(this RouteGroupBuilder group)
  {
    group.MapGet("/access", GetAccess);
    group.MapGet("/access/qr/{candidateId}", GetQr);
    return group;
  }

  private static IResult GetAccess(IConfiguration configuration) =>
    TypedResults.Ok(new AppAccessUrlService(configuration).GetView());

  private static IResult GetQr(string candidateId, IConfiguration configuration, HttpContext context)
  {
    AppAccessCandidate? candidate = new AppAccessUrlService(configuration).Find(candidateId);
    if (candidate is null)
    {
      return TypedResults.NotFound();
    }

    context.Response.Headers.CacheControl = "no-store";
    QrCode qrCode = QrCode.EncodeText(candidate.Url, QrCode.Ecc.Medium);
    return Results.Text(qrCode.ToSvgString(4), "image/svg+xml", Encoding.UTF8);
  }
}
