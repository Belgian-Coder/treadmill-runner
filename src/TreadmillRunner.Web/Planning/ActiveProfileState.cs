using Microsoft.JSInterop;

namespace TreadmillRunner.Web.Planning;

public sealed class ActiveProfileState(IJSRuntime jsRuntime)
{
  private const string StorageKey = "treadmillrunner.active-profile";
  private bool storageAvailable = true;
  private Guid? currentProfileId;
  public event Action<Guid?>? Changed;

  public async ValueTask<Guid?> GetAsync()
  {
    if (!storageAvailable) return currentProfileId;
    try
    {
      string? value = await jsRuntime.InvokeAsync<string?>("localStorage.getItem", StorageKey);
      currentProfileId = Guid.TryParse(value, out Guid profileId) ? profileId : null;
    }
    catch (JSException)
    {
      storageAvailable = false;
    }
    return currentProfileId;
  }

  public async ValueTask SetAsync(Guid profileId)
  {
    currentProfileId = profileId;
    if (storageAvailable)
    {
      try
      {
        await jsRuntime.InvokeVoidAsync("localStorage.setItem", StorageKey, profileId.ToString("D"));
      }
      catch (JSException)
      {
        storageAvailable = false;
      }
    }
    Changed?.Invoke(profileId);
  }

  public async ValueTask ClearAsync()
  {
    currentProfileId = null;
    if (storageAvailable)
    {
      try
      {
        await jsRuntime.InvokeVoidAsync("localStorage.removeItem", StorageKey);
      }
      catch (JSException)
      {
        storageAvailable = false;
      }
    }
    Changed?.Invoke(null);
  }
}
