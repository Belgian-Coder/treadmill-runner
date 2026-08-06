using System.Net;
using Microsoft.AspNetCore.Http;
using TreadmillRunner.Gateway.Garmin;

namespace TreadmillRunner.IntegrationTests;

public sealed class GarminCredentialTransportPolicyTests
{
  [Theory]
  [InlineData("127.0.0.1")]
  [InlineData("::1")]
  [InlineData("10.20.30.40")]
  [InlineData("172.16.0.1")]
  [InlineData("172.31.255.254")]
  [InlineData("192.168.1.20")]
  [InlineData("169.254.10.20")]
  [InlineData("::ffff:192.168.1.20")]
  [InlineData("fd12:3456:789a::20")]
  [InlineData("fe80::20")]
  public void Local_and_private_addresses_are_allowed_over_http(string value)
  {
    var context = new DefaultHttpContext();
    context.Request.Scheme = "http";
    context.Connection.RemoteIpAddress = IPAddress.Parse(value);

    Assert.True(GarminCredentialTransportPolicy.IsAllowed(context));
  }

  [Theory]
  [InlineData("8.8.8.8")]
  [InlineData("172.15.255.255")]
  [InlineData("172.32.0.1")]
  [InlineData("203.0.113.20")]
  [InlineData("2001:4860:4860::8888")]
  public void Public_addresses_are_rejected_over_http(string value)
  {
    var context = new DefaultHttpContext();
    context.Request.Scheme = "http";
    context.Connection.RemoteIpAddress = IPAddress.Parse(value);

    Assert.False(GarminCredentialTransportPolicy.IsAllowed(context));
  }

  [Fact]
  public void Missing_peer_address_is_rejected_over_http()
  {
    var context = new DefaultHttpContext();
    context.Request.Scheme = "http";

    Assert.False(GarminCredentialTransportPolicy.IsAllowed(context));
  }

  [Fact]
  public void Https_is_allowed_for_any_peer_address()
  {
    var context = new DefaultHttpContext();
    context.Request.Scheme = "https";
    context.Connection.RemoteIpAddress = IPAddress.Parse("203.0.113.20");

    Assert.True(GarminCredentialTransportPolicy.IsAllowed(context));
  }
}
