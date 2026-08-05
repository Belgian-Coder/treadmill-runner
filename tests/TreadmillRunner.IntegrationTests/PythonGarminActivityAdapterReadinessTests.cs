using Microsoft.Extensions.FileProviders;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Options;
using TreadmillRunner.Gateway.Garmin;

namespace TreadmillRunner.IntegrationTests;

public sealed class PythonGarminActivityAdapterReadinessTests : IAsyncLifetime
{
  private readonly string directory = Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", $"garmin-readiness-{Guid.NewGuid():N}");

  public Task InitializeAsync() { Directory.CreateDirectory(directory); return Task.CompletedTask; }
  public Task DisposeAsync() { if (Directory.Exists(directory)) Directory.Delete(directory, true); return Task.CompletedTask; }

  [Theory]
  [InlineData("{\"state\":\"ready\"}", GarminAdapterReadinessStates.Ready, true)]
  [InlineData("{\"state\":\"failed\",\"kind\":\"provider-unavailable\"}", GarminAdapterReadinessStates.DependencyMissing, false)]
  [InlineData("{\"state\":\"unexpected\"}", GarminAdapterReadinessStates.AdapterInvalid, false)]
  public async Task Probe_response_is_classified_without_exposing_paths(string response, string expectedState, bool canConnect)
  {
    string script = Path.Combine(directory, $"probe-{Guid.NewGuid():N}.py");
    string pythonLiteral = System.Text.Json.JsonSerializer.Serialize(response);
    await File.WriteAllTextAsync(script, $"import sys\nsys.stdin.readline()\nprint({pythonLiteral})\n");
    PythonGarminActivityAdapter adapter = Create(new()
    {
      PythonExecutable = "python",
      AdapterScriptPath = script,
      TimeoutSeconds = 10,
    });

    GarminAdapterReadiness readiness = await adapter.CheckAsync();

    Assert.Equal(expectedState, readiness.State);
    Assert.Equal(canConnect, readiness.CanConnect);
    Assert.DoesNotContain(directory, readiness.Message, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public async Task Missing_bundled_runtime_is_reported_safely()
  {
    string script = Path.Combine(directory, "adapter.py");
    await File.WriteAllTextAsync(script, "print('unused')");
    PythonGarminActivityAdapter adapter = Create(new()
    {
      PythonExecutable = Path.Combine(directory, "missing", "python.exe"),
      AdapterScriptPath = script,
    });

    GarminAdapterReadiness readiness = await adapter.CheckAsync();

    Assert.Equal(GarminAdapterReadinessStates.RuntimeMissing, readiness.State);
    Assert.False(readiness.CanConnect);
    Assert.DoesNotContain(directory, readiness.Message, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public async Task Relative_runtime_path_cannot_escape_the_release_content_root()
  {
    string script = Path.Combine(directory, "adapter.py");
    await File.WriteAllTextAsync(script, "print('unused')");
    PythonGarminActivityAdapter adapter = Create(new()
    {
      PythonExecutable = "..\\outside\\python.exe",
      AdapterScriptPath = script,
    });

    GarminAdapterReadiness readiness = await adapter.CheckAsync();

    Assert.Equal(GarminAdapterReadinessStates.AdapterInvalid, readiness.State);
    Assert.False(readiness.CanConnect);
    Assert.DoesNotContain("outside", readiness.Message, StringComparison.OrdinalIgnoreCase);
  }

  private PythonGarminActivityAdapter Create(GarminActivityAdapterOptions options) =>
    new(new StaticOptionsMonitor<GarminActivityAdapterOptions>(options), new TestEnvironment(directory));

  private sealed class StaticOptionsMonitor<T>(T value) : IOptionsMonitor<T>
  {
    public T CurrentValue => value;
    public T Get(string? name) => value;
    public IDisposable? OnChange(Action<T, string?> listener) => null;
  }

  private sealed class TestEnvironment(string contentRoot) : IHostEnvironment
  {
    public string EnvironmentName { get; set; } = Environments.Development;
    public string ApplicationName { get; set; } = "TreadmillRunner.Tests";
    public string ContentRootPath { get; set; } = contentRoot;
    public IFileProvider ContentRootFileProvider { get; set; } = new PhysicalFileProvider(contentRoot);
  }
}
