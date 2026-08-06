using System.Net;
using System.Net.Sockets;

namespace TreadmillRunner.Gateway.Garmin;

internal static class GarminCredentialTransportPolicy
{
  public static bool IsAllowed(HttpContext context) =>
    context.Request.IsHttps ||
    (context.Connection.RemoteIpAddress is { } address && IsLocalOrPrivate(address));

  internal static bool IsLocalOrPrivate(IPAddress address)
  {
    if (address.IsIPv4MappedToIPv6)
    {
      address = address.MapToIPv4();
    }

    if (IPAddress.IsLoopback(address))
    {
      return true;
    }

    byte[] bytes = address.GetAddressBytes();
    if (address.AddressFamily == AddressFamily.InterNetwork)
    {
      return bytes[0] == 10 ||
        (bytes[0] == 172 && bytes[1] is >= 16 and <= 31) ||
        (bytes[0] == 192 && bytes[1] == 168) ||
        (bytes[0] == 169 && bytes[1] == 254);
    }

    return address.AddressFamily == AddressFamily.InterNetworkV6 &&
      ((bytes[0] & 0xfe) == 0xfc ||
       (bytes[0] == 0xfe && (bytes[1] & 0xc0) == 0x80));
  }
}
