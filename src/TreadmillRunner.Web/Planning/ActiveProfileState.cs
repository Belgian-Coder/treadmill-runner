using Microsoft.JSInterop;

namespace TreadmillRunner.Web.Planning;

public sealed class ActiveProfileState(IJSRuntime jsRuntime)
{
  private const string StorageKey = "treadmillrunner.active-profile";
  public event Action<Guid?>? Changed;

  public async ValueTask<Guid?> GetAsync()
  {
    string? value = await jsRuntime.InvokeAsync<string?>("localStorage.getItem", StorageKey);
    return Guid.TryParse(value, out Guid profileId) ? profileId : null;
  }

  public async ValueTask SetAsync(Guid profileId)
  {
    await jsRuntime.InvokeVoidAsync("localStorage.setItem", StorageKey, profileId.ToString("D"));
    Changed?.Invoke(profileId);
  }

  public async ValueTask ClearAsync()
  {
    await jsRuntime.InvokeVoidAsync("localStorage.removeItem", StorageKey);
    Changed?.Invoke(null);
  }
}
