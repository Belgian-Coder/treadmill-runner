using Microsoft.AspNetCore.Components.WebAssembly.Hosting;
using TreadmillRunner.Web.Planning;
using TreadmillRunner.Web;

var builder = WebAssemblyHostBuilder.CreateDefault(args);

builder.Services.AddScoped<ClientRuntimeState>();
builder.Services.AddScoped(_ => TimeProvider.System);
builder.Services.AddScoped(services => new HttpClient(new ClientBuildFingerprintHandler(services.GetRequiredService<ClientRuntimeState>())
{
  InnerHandler = new HttpClientHandler(),
})
{
  BaseAddress = new Uri(builder.HostEnvironment.BaseAddress),
});
builder.Services.AddScoped(static services => new ActiveProfileState(
  services.GetRequiredService<Microsoft.JSInterop.IJSRuntime>()));
builder.Services.AddScoped<TreadmillRunner.Web.Live.GatewayConnectionSupervisor>();
builder.Services.AddScoped<TreadmillRunner.Web.Live.ControlFocusState>();

await builder.Build().RunAsync();

public partial class Program;
