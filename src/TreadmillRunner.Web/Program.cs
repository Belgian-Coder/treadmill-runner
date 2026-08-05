using Microsoft.AspNetCore.Components.WebAssembly.Hosting;
using TreadmillRunner.Web.Planning;

var builder = WebAssemblyHostBuilder.CreateDefault(args);

builder.Services.AddScoped(_ => new HttpClient
{
  BaseAddress = new Uri(builder.HostEnvironment.BaseAddress),
});
builder.Services.AddScoped(static services => new ActiveProfileState(
  services.GetRequiredService<Microsoft.JSInterop.IJSRuntime>()));

await builder.Build().RunAsync();

public partial class Program;
