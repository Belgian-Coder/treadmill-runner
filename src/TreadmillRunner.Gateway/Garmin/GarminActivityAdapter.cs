using System.Diagnostics;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace TreadmillRunner.Gateway.Garmin;

public sealed class GarminActivityAdapterOptions
{
  public const string SectionName = "GarminActivityUpload";
  public string PythonExecutable { get; set; } = "python";
  public string AdapterScriptPath { get; set; } = "tools/garmin/garmin_activity_adapter.py";
  public string? PythonPath { get; set; }
  public int TimeoutSeconds { get; set; } = 45;
}

public sealed record GarminAdapterMessage(
  string State,
  string? Kind,
  string? Message,
  string? AccountLabel,
  string? TokenStore,
  string? RemoteId);

public sealed class GarminAdapterUnavailableException(string message, Exception? innerException = null) : Exception(message, innerException);
public sealed class GarminAdapterAmbiguousResultException(string message, Exception? innerException = null) : Exception(message, innerException);

public interface IGarminActivityAdapter
{
  Task<IGarminAdapterConnectProcess> BeginConnectAsync(string email, string password, CancellationToken cancellationToken);
  Task<GarminAdapterMessage> UploadAsync(string tokenStore, string activityPath, CancellationToken cancellationToken);
}

public interface IGarminAdapterConnectProcess : IAsyncDisposable
{
  Task<GarminAdapterMessage> ReadAsync(CancellationToken cancellationToken);
  Task<GarminAdapterMessage> CompleteMfaAsync(string code, CancellationToken cancellationToken);
}

public sealed class GarminAdapterConnectProcess : IGarminAdapterConnectProcess
{
  private const int MaximumResponseCharacters = 65_536;
  private const int MaximumTokenStoreCharacters = 32_768;
  private readonly Process _process;
  private readonly TimeSpan _timeout;

  internal GarminAdapterConnectProcess(Process process, TimeSpan timeout) { _process = process; _timeout = timeout; }
  public Task<GarminAdapterMessage> ReadAsync(CancellationToken cancellationToken) => ReadMessageAsync(_process, _timeout, cancellationToken);
  public async Task<GarminAdapterMessage> CompleteMfaAsync(string code, CancellationToken cancellationToken)
  {
    await _process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(new { mfaCode = code }).AsMemory(), cancellationToken);
    await _process.StandardInput.FlushAsync(cancellationToken);
    return await ReadAsync(cancellationToken);
  }
  public async ValueTask DisposeAsync()
  {
    try { _process.StandardInput.Close(); } catch (InvalidOperationException) { }
    if (!_process.HasExited) { _process.Kill(entireProcessTree: true); await _process.WaitForExitAsync(); }
    _process.Dispose();
  }

  internal static async Task<GarminAdapterMessage> ReadMessageAsync(Process process, TimeSpan timeout, CancellationToken cancellationToken)
  {
    using var timeoutSource = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
    timeoutSource.CancelAfter(timeout);
    string? line;
    try { line = await ReadBoundedLineAsync(process.StandardOutput, timeoutSource.Token); }
    catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
    {
      throw new TimeoutException("The Garmin adapter did not respond before its bounded timeout.");
    }
    if (string.IsNullOrWhiteSpace(line)) throw new GarminAdapterAmbiguousResultException("The Garmin adapter ended without a response.");
    try
    {
      GarminAdapterMessage message = JsonSerializer.Deserialize<GarminAdapterMessage>(line, new JsonSerializerOptions(JsonSerializerDefaults.Web))
        ?? throw new GarminAdapterAmbiguousResultException("The Garmin adapter returned an empty response.");
      if (message.TokenStore?.Length > MaximumTokenStoreCharacters)
        throw new GarminAdapterAmbiguousResultException("The Garmin adapter returned an oversized credential envelope.");
      return message;
    }
    catch (JsonException exception)
    {
      throw new GarminAdapterAmbiguousResultException("The Garmin adapter returned an invalid response.", exception);
    }
  }

  private static async Task<string?> ReadBoundedLineAsync(StreamReader reader, CancellationToken cancellationToken)
  {
    var buffer = new char[1];
    var response = new System.Text.StringBuilder(capacity: 1024);
    while (response.Length <= MaximumResponseCharacters)
    {
      int read = await reader.ReadAsync(buffer.AsMemory(), cancellationToken);
      if (read == 0) return response.Length == 0 ? null : response.ToString();
      if (buffer[0] == '\n') return response.ToString().TrimEnd('\r');
      response.Append(buffer[0]);
    }
    throw new GarminAdapterAmbiguousResultException("The Garmin adapter returned an oversized response.");
  }
}

public sealed class PythonGarminActivityAdapter(
  IOptionsMonitor<GarminActivityAdapterOptions> options,
  IHostEnvironment environment) : IGarminActivityAdapter
{
  public async Task<IGarminAdapterConnectProcess> BeginConnectAsync(string email, string password, CancellationToken cancellationToken)
  {
    GarminActivityAdapterOptions current = options.CurrentValue;
    Process process = Start(current);
    try
    {
      await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(new { operation = "connect", email, password }).AsMemory(), cancellationToken);
      await process.StandardInput.FlushAsync(cancellationToken);
      return new GarminAdapterConnectProcess(process, Timeout(current));
    }
    catch
    {
      await StopAsync(process);
      throw;
    }
  }

  public async Task<GarminAdapterMessage> UploadAsync(string tokenStore, string activityPath, CancellationToken cancellationToken)
  {
    GarminActivityAdapterOptions current = options.CurrentValue;
    Process process = Start(current);
    try
    {
      await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(new { operation = "upload", tokenStore, activityPath }).AsMemory(), cancellationToken);
      await process.StandardInput.FlushAsync(cancellationToken);
      process.StandardInput.Close();
      GarminAdapterMessage message = await GarminAdapterConnectProcess.ReadMessageAsync(process, Timeout(current), cancellationToken);
      await process.WaitForExitAsync(cancellationToken).WaitAsync(Timeout(current), cancellationToken);
      return message;
    }
    catch (Exception exception) when (exception is not (GarminAdapterUnavailableException or GarminAdapterAmbiguousResultException or TimeoutException or OperationCanceledException))
    {
      throw new GarminAdapterAmbiguousResultException("The Garmin adapter failed after the upload request may have started.", exception);
    }
    finally
    {
      await StopAsync(process);
    }
  }

  private Process Start(GarminActivityAdapterOptions current)
  {
    string script = Path.IsPathRooted(current.AdapterScriptPath)
      ? current.AdapterScriptPath
      : Path.GetFullPath(current.AdapterScriptPath, environment.ContentRootPath);
    if (!File.Exists(script)) throw new GarminAdapterUnavailableException("The unsupported Garmin activity adapter is not installed.");
    var start = new ProcessStartInfo
    {
      FileName = current.PythonExecutable,
      RedirectStandardInput = true,
      RedirectStandardOutput = true,
      RedirectStandardError = true,
      UseShellExecute = false,
      CreateNoWindow = true,
      WorkingDirectory = Path.GetDirectoryName(script)!,
    };
    start.ArgumentList.Add("-B");
    start.ArgumentList.Add(script);
    if (!string.IsNullOrWhiteSpace(current.PythonPath)) start.Environment["PYTHONPATH"] = current.PythonPath;
    try
    {
      return Process.Start(start) ?? throw new GarminAdapterUnavailableException("The Garmin adapter process could not start.");
    }
    catch (GarminAdapterUnavailableException) { throw; }
    catch (Exception exception) when (exception is System.ComponentModel.Win32Exception or InvalidOperationException)
    {
      throw new GarminAdapterUnavailableException("The unsupported Garmin adapter runtime is unavailable.", exception);
    }
  }

  private static TimeSpan Timeout(GarminActivityAdapterOptions current) => TimeSpan.FromSeconds(Math.Clamp(current.TimeoutSeconds, 10, 90));

  private static async Task StopAsync(Process process)
  {
    try
    {
      if (!process.HasExited)
      {
        process.Kill(entireProcessTree: true);
        await process.WaitForExitAsync();
      }
    }
    catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception) { }
    finally { process.Dispose(); }
  }
}
