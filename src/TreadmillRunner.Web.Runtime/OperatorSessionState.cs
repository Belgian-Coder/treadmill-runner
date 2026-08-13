using System.Net;
using Microsoft.AspNetCore.Components;
using Microsoft.JSInterop;

namespace TreadmillRunner.Web.Runtime;

public sealed class OperatorSessionState(IJSRuntime js)
{
  private const string StorageKey = "treadmillrunner.operator-token";
  private readonly SemaphoreSlim gate = new(1, 1);
  private bool loaded;
  public string? Token { get; private set; }
  public bool HasSession => !string.IsNullOrWhiteSpace(Token);
  public event Action? Changed;

  public async ValueTask<string?> GetTokenAsync(CancellationToken cancellationToken = default)
  {
    if (loaded) return Token;
    await gate.WaitAsync(cancellationToken);
    try
    {
      if (!loaded)
      {
        try
        {
          Token = await js.InvokeAsync<string?>("sessionStorage.getItem", cancellationToken, StorageKey);
        }
        catch (JSException)
        {
          Token = null;
        }
        loaded = true;
      }
      return Token;
    }
    finally
    {
      gate.Release();
    }
  }

  public async Task SetAsync(string token)
  {
    Token = token;
    loaded = true;
    try
    {
      await js.InvokeVoidAsync("sessionStorage.setItem", StorageKey, token);
    }
    catch (JSException)
    {
      // Privacy modes may disable session storage. Keep the token in memory for this page lifetime only.
    }
    Changed?.Invoke();
  }

  public async Task ClearAsync()
  {
    Token = null;
    loaded = true;
    try
    {
      await js.InvokeVoidAsync("sessionStorage.removeItem", StorageKey);
    }
    catch (JSException)
    {
      // The in-memory session is already cleared.
    }
    Changed?.Invoke();
  }
}

public sealed class OperatorAccessHandler(OperatorSessionState session, NavigationManager navigation) : DelegatingHandler
{
  protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
  {
    string? token = await session.GetTokenAsync(cancellationToken);
    Uri? requestUri = request.RequestUri;
    Uri applicationBase = new(navigation.BaseUri, UriKind.Absolute);
    bool sameApplication = requestUri is null || !requestUri.IsAbsoluteUri || applicationBase.IsBaseOf(requestUri);
    if (sameApplication && !string.IsNullOrWhiteSpace(token))
    {
      request.Headers.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", token);
    }
    HttpResponseMessage response = await base.SendAsync(request, cancellationToken);
    if (response.StatusCode == HttpStatusCode.Unauthorized && !request.RequestUri!.AbsolutePath.EndsWith("/operator/login", StringComparison.OrdinalIgnoreCase))
    {
      await session.ClearAsync();
    }
    return response;
  }
}
