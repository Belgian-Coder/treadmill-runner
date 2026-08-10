using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Text;

namespace TreadmillRunner.E2ETests;

public sealed class GatewayFixture : IAsyncLifetime
{
  private const string TemplateDatabaseEnvironmentVariable = "TreadmillRunner__E2ETemplateDatabasePath";
  private static readonly SemaphoreSlim databaseTemplateGate = new(1, 1);
  private static readonly string fallbackTemplateDatabasePath = Path.Combine(
    Path.GetTempPath(),
    $"treadmillrunner-e2e-template-{Environment.ProcessId}.db");
  private static bool fallbackTemplateReady;
  private readonly HttpClient httpClient = new();
  private readonly object galleryScenarioSync = new();
  private readonly object gatewayErrorSync = new();
  private readonly StringBuilder gatewayErrors = new();
  private readonly string databasePath = Path.Combine(Path.GetTempPath(), $"treadmillrunner-e2e-{Guid.NewGuid():N}.db");
  private Task<GalleryScenario>? galleryScenario;
  private Process? gatewayProcess;

  static GatewayFixture()
  {
    AppDomain.CurrentDomain.ProcessExit += static (_, _) =>
    {
      TryDeleteDatabaseFile(fallbackTemplateDatabasePath);
      TryDeleteDatabaseFile(fallbackTemplateDatabasePath + "-shm");
      TryDeleteDatabaseFile(fallbackTemplateDatabasePath + "-wal");
    };
  }

  public Uri BaseAddress { get; private set; } = null!;

  public string ProjectRoot { get; } = FindProjectRoot();

  public Task<GalleryScenario> GetOrCreateGalleryScenarioAsync()
  {
    lock (galleryScenarioSync)
    {
      return galleryScenario ??= GalleryScenario.CreateAsync(BaseAddress);
    }
  }

  public async Task InitializeAsync()
  {
    await PrepareDatabaseAsync();
    int port = ReserveTcpPort();
    BaseAddress = new Uri($"http://127.0.0.1:{port}", UriKind.Absolute);

    string hostPath = Path.Combine(ProjectRoot, "artifacts", "e2e-host", "TreadmillRunner.Gateway.exe");
    if (!File.Exists(hostPath))
    {
      throw new FileNotFoundException(
          "The published E2E gateway was not found. Run eng/playwright.ps1 to build it first.",
          hostPath);
    }
    AssertPublishedHostIsFresh(hostPath);

    ProcessStartInfo startInfo = new(hostPath)
    {
      WorkingDirectory = Path.GetDirectoryName(hostPath)!,
      UseShellExecute = false,
      CreateNoWindow = true,
      RedirectStandardOutput = true,
      RedirectStandardError = true,
    };
    startInfo.Environment["ASPNETCORE_URLS"] = BaseAddress.AbsoluteUri.TrimEnd('/');
    startInfo.Environment["ASPNETCORE_ENVIRONMENT"] = "Development";
    startInfo.Environment["Gateway__Urls"] = BaseAddress.AbsoluteUri.TrimEnd('/');
    startInfo.Environment["TreadmillRunner__Mode"] = "Simulator";
    startInfo.Environment["Persistence__DatabasePath"] = databasePath;

    gatewayProcess = Process.Start(startInfo)
        ?? throw new InvalidOperationException("The simulator gateway process could not be started.");
    gatewayProcess.OutputDataReceived += static (_, _) => { };
    gatewayProcess.ErrorDataReceived += (_, args) => CaptureGatewayError(args.Data);
    gatewayProcess.BeginOutputReadLine();
    gatewayProcess.BeginErrorReadLine();

    using CancellationTokenSource timeout = new(TimeSpan.FromSeconds(45));
    while (!timeout.IsCancellationRequested)
    {
      if (gatewayProcess.HasExited)
      {
        string errors;
        lock (gatewayErrorSync)
        {
          errors = gatewayErrors.ToString();
        }
        throw new InvalidOperationException($"The simulator gateway exited before becoming ready.{Environment.NewLine}{errors}");
      }

      try
      {
        using HttpResponseMessage response = await httpClient.GetAsync(
            new Uri(BaseAddress, "/health/ready"),
            timeout.Token);
        if (response.StatusCode == HttpStatusCode.OK)
        {
          return;
        }
      }
      catch (HttpRequestException)
      {
        // Kestrel has not bound the port yet.
      }

      await Task.Delay(TimeSpan.FromMilliseconds(250), timeout.Token);
    }

    throw new TimeoutException("The simulator gateway did not become ready within 45 seconds.");
  }

  private void AssertPublishedHostIsFresh(string hostPath)
  {
    DateTime hostTimestampUtc = File.GetLastWriteTimeUtc(hostPath);
    string sourceRoot = Path.Combine(ProjectRoot, "src");
    string[] runtimeExtensions = [".cs", ".razor", ".csproj", ".json", ".resx", ".js", ".css"];
    string? newestInput = Directory.EnumerateFiles(sourceRoot, "*", SearchOption.AllDirectories)
      .Where(path => !path.Contains($"{Path.DirectorySeparatorChar}bin{Path.DirectorySeparatorChar}", StringComparison.OrdinalIgnoreCase) &&
        !path.Contains($"{Path.DirectorySeparatorChar}obj{Path.DirectorySeparatorChar}", StringComparison.OrdinalIgnoreCase) &&
        runtimeExtensions.Contains(Path.GetExtension(path), StringComparer.OrdinalIgnoreCase))
      .OrderByDescending(File.GetLastWriteTimeUtc)
      .FirstOrDefault();
    if (newestInput is null || File.GetLastWriteTimeUtc(newestInput) <= hostTimestampUtc) return;

    throw new InvalidOperationException(
      $"The published E2E gateway is older than '{Path.GetRelativePath(ProjectRoot, newestInput)}'. " +
      "Run eng/playwright.ps1 without -ReuseBuild before executing browser tests directly.");
  }

  public Task DisposeAsync()
  {
    httpClient.Dispose();
    if (gatewayProcess is { HasExited: false })
    {
      gatewayProcess.Kill(entireProcessTree: true);
      gatewayProcess.WaitForExit(TimeSpan.FromSeconds(10));
    }

    gatewayProcess?.Dispose();
    TryDeleteDatabaseFile(databasePath);
    TryDeleteDatabaseFile(databasePath + "-shm");
    TryDeleteDatabaseFile(databasePath + "-wal");
    return Task.CompletedTask;
  }

  private static void TryDeleteDatabaseFile(string path)
  {
    try
    {
      File.Delete(path);
    }
    catch (IOException)
    {
      // Test artifacts remain recoverable in the temporary directory if SQLite has not released a handle yet.
    }
  }

  private void CaptureGatewayError(string? line)
  {
    if (string.IsNullOrWhiteSpace(line))
    {
      return;
    }

    lock (gatewayErrorSync)
    {
      const int MaximumCapturedCharacters = 16 * 1024;
      if (gatewayErrors.Length < MaximumCapturedCharacters)
      {
        gatewayErrors.AppendLine(line);
      }
    }
  }

  private static int ReserveTcpPort()
  {
    using TcpListener listener = new(IPAddress.Loopback, 0);
    listener.Start();
    return ((IPEndPoint)listener.LocalEndpoint).Port;
  }

  private async Task PrepareDatabaseAsync()
  {
    string? configuredTemplate = Environment.GetEnvironmentVariable(TemplateDatabaseEnvironmentVariable);
    if (!string.IsNullOrWhiteSpace(configuredTemplate) && File.Exists(configuredTemplate))
    {
      File.Copy(configuredTemplate, databasePath, overwrite: false);
      return;
    }

    await databaseTemplateGate.WaitAsync();
    try
    {
      if (!fallbackTemplateReady || !File.Exists(fallbackTemplateDatabasePath))
      {
        TryDeleteDatabaseFile(fallbackTemplateDatabasePath);
        TryDeleteDatabaseFile(fallbackTemplateDatabasePath + "-shm");
        TryDeleteDatabaseFile(fallbackTemplateDatabasePath + "-wal");
        await ApplyMigrationsAsync(fallbackTemplateDatabasePath);
        fallbackTemplateReady = true;
      }
    }
    finally
    {
      databaseTemplateGate.Release();
    }

    File.Copy(fallbackTemplateDatabasePath, databasePath, overwrite: false);
  }

  private async Task ApplyMigrationsAsync(string targetDatabasePath)
  {
    string script = Path.Combine(ProjectRoot, "eng", "database.ps1");
    ProcessStartInfo startInfo = new("pwsh.exe")
    {
      WorkingDirectory = ProjectRoot,
      UseShellExecute = false,
      CreateNoWindow = true,
      RedirectStandardOutput = false,
      RedirectStandardError = false,
    };
    startInfo.ArgumentList.Add("-NoProfile");
    startInfo.ArgumentList.Add("-File");
    startInfo.ArgumentList.Add(script);
    startInfo.ArgumentList.Add("-Action");
    startInfo.ArgumentList.Add("Update");
    startInfo.ArgumentList.Add("-DatabasePath");
    startInfo.ArgumentList.Add(targetDatabasePath);
    using Process process = Process.Start(startInfo)
      ?? throw new InvalidOperationException("The explicit database migration process could not be started.");
    await process.WaitForExitAsync();
    if (process.ExitCode != 0)
    {
      throw new InvalidOperationException("Explicit E2E database migration failed. Review the test process output above.");
    }
  }

  private static string FindProjectRoot()
  {
    DirectoryInfo? directory = new(AppContext.BaseDirectory);
    while (directory is not null)
    {
      if (File.Exists(Path.Combine(directory.FullName, "TreadmillRunner.slnx")))
      {
        return directory.FullName;
      }

      directory = directory.Parent;
    }

    throw new DirectoryNotFoundException("Could not locate the TreadmillRunner repository root.");
  }
}
