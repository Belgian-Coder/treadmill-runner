using Microsoft.JSInterop;

namespace TreadmillRunner.Web.Planning;

public sealed class ActiveProfileState(IJSRuntime jsRuntime)
{
  private const string StorageKey = "treadmillrunner.active-profile";

  public async ValueTask<Guid?> GetAsync()
  {
    string? value = await jsRuntime.InvokeAsync<string?>("localStorage.getItem", StorageKey);
    return Guid.TryParse(value, out Guid profileId) ? profileId : null;
  }

  public ValueTask SetAsync(Guid profileId) =>
    jsRuntime.InvokeVoidAsync("localStorage.setItem", StorageKey, profileId.ToString("D"));

  public ValueTask ClearAsync() => jsRuntime.InvokeVoidAsync("localStorage.removeItem", StorageKey);
}
