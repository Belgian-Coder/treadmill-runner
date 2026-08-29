using System.Diagnostics;
using System.IO.Compression;

namespace TreadmillRunner.IntegrationTests;

public sealed class ReleaseScriptContractTests
{
  private static readonly string ProjectRoot = FindProjectRoot();

  [Theory]
  [InlineData("initialize-release-signer.ps1")]
  [InlineData("install-stable-update-feed.ps1")]
  [InlineData("package-update.ps1")]
  [InlineData("create-update-acceptance-feed.ps1")]
  [InlineData("select-update-acceptance-fixture.ps1")]
  [InlineData("install-gateway-service.ps1")]
  [InlineData("accept-gateway-service.ps1")]
  [InlineData("publish-release.ps1")]
  [InlineData("Install-TreadmillRunner.ps1")]
  [InlineData("new-installer-bundle.ps1")]
  [InlineData("create-github-release.ps1")]
  [InlineData("test.ps1")]
  [InlineData("playwright.ps1")]
  [InlineData("inspect-service-recovery.ps1")]
  [InlineData("validate-connectiq.ps1")]
  [InlineData("verify-change.ps1")]
  [InlineData("physical-acceptance-preflight.ps1")]
  [InlineData("verify-recovery-acceptance.ps1")]
  [InlineData("new-operator-access-secret.ps1")]
  public async Task Release_script_has_valid_PowerShell_syntax(string scriptName)
  {
    string scriptPath = Path.Combine(ProjectRoot, "eng", scriptName);
    var startInfo = new ProcessStartInfo
    {
      FileName = "powershell.exe",
      UseShellExecute = false,
      CreateNoWindow = true,
      RedirectStandardError = true,
      RedirectStandardOutput = true,
    };
    startInfo.ArgumentList.Add("-NoProfile");
    startInfo.ArgumentList.Add("-NonInteractive");
    startInfo.ArgumentList.Add("-Command");
    string escapedPath = scriptPath.Replace("'", "''", StringComparison.Ordinal);
    startInfo.ArgumentList.Add(
      $"$tokens = $null; $errors = $null; [System.Management.Automation.Language.Parser]::ParseFile('{escapedPath}', [ref]$tokens, [ref]$errors) | Out-Null; if ($errors.Count -gt 0) {{ $errors | ForEach-Object {{ [Console]::Error.WriteLine($_) }}; exit 1 }}");
    using Process process = Process.Start(startInfo)!;
    string error = await process.StandardError.ReadToEndAsync();
    await process.WaitForExitAsync();

    Assert.True(process.ExitCode == 0, $"{scriptName} has invalid PowerShell syntax: {error}");
  }

  [Fact]
  public void GitHub_release_and_offline_install_scripts_preserve_the_local_signer_and_signed_bundle_contract()
  {
    string release = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "create-github-release.ps1"));
    string package = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "package-update.ps1"));
    string installer = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "Install-TreadmillRunner.ps1"));
    string installerBundle = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "new-installer-bundle.ps1"));
    string test = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "test.ps1"));
    string build = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "build.ps1"));

    Assert.Contains("belgian-coder/treadmill-runner", release, StringComparison.Ordinal);
    Assert.Contains("gh auth status", release, StringComparison.Ordinal);
    Assert.Contains("stable.manifest.json", release, StringComparison.Ordinal);
    Assert.Contains("offline-update.zip", release, StringComparison.Ordinal);
    Assert.Contains("--draft", release, StringComparison.Ordinal);
    Assert.Contains("--verify-tag", release, StringComparison.Ordinal);
    Assert.Contains("git tag -a $tag", release, StringComparison.Ordinal);
    Assert.Contains("git fetch origin \"refs/tags/${tag}:refs/tags/${tag}\"", release, StringComparison.Ordinal);
    Assert.Contains("git rev-list -n 1", release, StringComparison.Ordinal);
    Assert.Contains("git cat-file -t", release, StringComparison.Ordinal);
    Assert.Contains("not an annotated tag", release, StringComparison.OrdinalIgnoreCase);
    Assert.Contains("Tags are never moved", release, StringComparison.Ordinal);
    Assert.Contains("must be newer than every published release", release, StringComparison.Ordinal);
    Assert.Contains("Assert-OriginRepository", release, StringComparison.Ordinal);
    Assert.Contains("git remote get-url --push origin", release, StringComparison.Ordinal);
    Assert.Contains("Get-GhReleaseView", release, StringComparison.Ordinal);
    Assert.Contains("Could not inspect GitHub release", release, StringComparison.Ordinal);
    Assert.Contains("not found", release, StringComparison.OrdinalIgnoreCase);
    Assert.Contains("SkipValidation is allowed only when resuming", release, StringComparison.Ordinal);
    Assert.Contains("TREADMILLRUNNER_UPDATE_SHOWCASE = '0'", release, StringComparison.Ordinal);
    Assert.Contains("full-acceptance.json", release, StringComparison.Ordinal);
    Assert.Contains("sourceRevision -eq $head", release, StringComparison.Ordinal);
    Assert.Contains("FromHours(8)", release, StringComparison.Ordinal);
    Assert.Contains("browserAcceptanceRequired", release, StringComparison.Ordinal);
    Assert.Contains("browserAccepted", release, StringComparison.Ordinal);
    Assert.Contains("-NoBrowser:(-not $browserAcceptanceRequired)", release, StringComparison.Ordinal);
    Assert.Contains("verify-change.ps1') -Configuration Release -Full", release, StringComparison.Ordinal);
    Assert.DoesNotContain("playwright.ps1') -Configuration Release -TimeoutMinutes 7", release, StringComparison.Ordinal);
    Assert.Contains("Release validation changed tracked or untracked files", release, StringComparison.Ordinal);
    Assert.Contains("origin/main changed during release validation", release, StringComparison.Ordinal);
    Assert.Contains("validated source changed while release assets were being prepared", release, StringComparison.OrdinalIgnoreCase);
    Assert.Contains("ReleaseNotes must exactly match the existing draft release", release, StringComparison.Ordinal);
    Assert.Contains("build-metadata.json", release, StringComparison.Ordinal);
    Assert.Contains("sourceRevision -ne $Head", release, StringComparison.Ordinal);
    Assert.Contains("ExpectedBuildId", release, StringComparison.Ordinal);
    Assert.Contains("stable manifest does not match", release, StringComparison.OrdinalIgnoreCase);
    Assert.Contains("VerifyData", release, StringComparison.Ordinal);
    Assert.Contains("offline update bundle must contain exactly", release, StringComparison.OrdinalIgnoreCase);
    Assert.Contains("installer bundle build provenance", release, StringComparison.OrdinalIgnoreCase);
    Assert.Contains("staged output was discarded", release, StringComparison.OrdinalIgnoreCase);
    Assert.Contains("OutputRoot $stagingRoot", release, StringComparison.Ordinal);
    Assert.DoesNotContain("SHA256]::HashData", release, StringComparison.Ordinal);
    Assert.DoesNotContain("Convert]::ToHexString", release, StringComparison.Ordinal);
    Assert.Contains("draft release contains unexpected assets", release, StringComparison.OrdinalIgnoreCase);
    Assert.Contains("gh release upload $tag @assets", release, StringComparison.Ordinal);
    Assert.Contains("--clobber", release, StringComparison.Ordinal);
    Assert.Contains("existing draft", release, StringComparison.OrdinalIgnoreCase);
    Assert.DoesNotContain("GitHubToken", release, StringComparison.OrdinalIgnoreCase);
    Assert.DoesNotContain("ConvertTo-SecureString", release, StringComparison.OrdinalIgnoreCase);
    Assert.Contains("offline-update.zip", package, StringComparison.Ordinal);
    Assert.Contains("NoCompression", package, StringComparison.Ordinal);
    Assert.Contains("#Requires -RunAsAdministrator", installer, StringComparison.Ordinal);
    Assert.Contains("Microsoft\\.AspNetCore\\.App 10", installer, StringComparison.Ordinal);
    Assert.Contains("NetworkCategory -eq 'Private'", installer, StringComparison.Ordinal);
    Assert.Contains("TreadmillRunner setup", installer, StringComparison.Ordinal);
    Assert.Contains("INSTALL.txt", installerBundle, StringComparison.Ordinal);
    Assert.Contains("docs/installation.md", installerBundle, StringComparison.Ordinal);
    Assert.Contains("-p:WasmBuildNative=false", test, StringComparison.Ordinal);
    Assert.Contains("-p:InvariantGlobalization=false", test, StringComparison.Ordinal);
    Assert.Contains("SkipNativeWeb", build, StringComparison.Ordinal);
    Assert.Contains("-p:WasmBuildNative=false", build, StringComparison.Ordinal);
    Assert.Contains("-p:InvariantGlobalization=false", build, StringComparison.Ordinal);
  }

  [Fact]
  public void GitHub_Actions_is_completely_disabled_and_release_builds_stay_local()
  {
    string workflows = Path.Combine(ProjectRoot, ".github", "workflows");
    string dependabot = Path.Combine(ProjectRoot, ".github", "dependabot.yml");
    string instructions = File.ReadAllText(Path.Combine(ProjectRoot, "AGENTS.md"));

    Assert.False(Directory.Exists(workflows) && Directory.EnumerateFiles(workflows, "*", SearchOption.AllDirectories).Any());
    Assert.False(File.Exists(dependabot));
    Assert.Contains("GitHub Actions is disabled", instructions, StringComparison.Ordinal);
    Assert.Contains("all validation, building, signing, and packaging runs on the release workstation", instructions, StringComparison.Ordinal);
  }

  [Fact]
  public void Local_publish_embeds_a_content_fingerprint_and_records_release_provenance()
  {
    string script = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "publish-release.ps1"));
    string buildProps = File.ReadAllText(Path.Combine(ProjectRoot, "Directory.Build.props"));
    string webProject = File.ReadAllText(Path.Combine(ProjectRoot, "src", "TreadmillRunner.Web", "TreadmillRunner.Web.csproj"));
    string gatewayProject = File.ReadAllText(Path.Combine(ProjectRoot, "src", "TreadmillRunner.Gateway", "TreadmillRunner.Gateway.csproj"));

    Assert.Contains("git -C $projectRoot diff --binary HEAD -- src Directory.Build.props", script, StringComparison.Ordinal);
    Assert.Contains("$sourceDiff = @(& git -C $projectRoot diff --binary HEAD -- src Directory.Build.props)", script, StringComparison.Ordinal);
    Assert.Contains("The reviewed source diff could not be determined.", script, StringComparison.Ordinal);
    Assert.Contains("git -C $projectRoot ls-files --others --exclude-standard -- src Directory.Build.props", script, StringComparison.Ordinal);
    Assert.Contains("-p:TreadmillRunnerBuildId=$buildId", script, StringComparison.Ordinal);
    Assert.Contains("-p:InformationalVersion=\"$Version+$buildId\"", script, StringComparison.Ordinal);
    Assert.Contains("build-metadata.json", script, StringComparison.Ordinal);
    Assert.Contains("sourceRevision = $headRevision", script, StringComparison.Ordinal);
    Assert.Contains("buildId = $buildId", script, StringComparison.Ordinal);
    Assert.Contains("TreadmillRunnerBuildId", buildProps, StringComparison.Ordinal);
    Assert.Contains("dotnet workload list", script, StringComparison.Ordinal);
    Assert.Contains("wasm-tools", script, StringComparison.Ordinal);
    Assert.Contains("clean-wasm-publish.ps1", script, StringComparison.Ordinal);
    Assert.Contains("<PublishTrimmed>true</PublishTrimmed>", webProject, StringComparison.Ordinal);
    Assert.Contains("<WasmEnableHotReload>false</WasmEnableHotReload>", webProject, StringComparison.Ordinal);
    Assert.Contains("GlobalPropertiesToRemove=\"PublishTrimmed\"", gatewayProject, StringComparison.Ordinal);
  }

  [Fact]
  public void Playwright_restores_the_gateway_graph_after_cleaning_publish_state()
  {
    string script = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "playwright.ps1"));
    int cleanup = script.IndexOf("& $wasmCleaner -Configuration $Configuration", StringComparison.Ordinal);
    int restore = script.IndexOf("dotnet restore $gatewayProject --locked-mode", StringComparison.Ordinal);
    int publish = script.IndexOf("dotnet publish $gatewayProject", StringComparison.Ordinal);

    Assert.True(cleanup >= 0, "The focused browser build must clean stale WebAssembly publish state.");
    Assert.True(restore > cleanup, "The Gateway graph must be restored after WebAssembly cleanup.");
    Assert.True(publish > restore, "The no-restore Gateway publish must follow the post-clean restore.");
  }

  [Fact]
  public void Focused_test_runner_restores_fresh_worktrees_and_rejects_zero_matches()
  {
    string script = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "test.ps1"));

    Assert.Contains("if ($Build)", script, StringComparison.Ordinal);
    Assert.Contains("dotnet restore $solution --locked-mode", script, StringComparison.Ordinal);
    Assert.Contains("LogFilePrefix=$runStamp", script, StringComparison.Ordinal);
    Assert.Contains("the filter executed zero tests", script, StringComparison.Ordinal);
    Assert.Contains("UnitTestResult", script, StringComparison.Ordinal);
    Assert.Contains("Set-NativeProcessArguments", script, StringComparison.Ordinal);

    string browser = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "playwright.ps1"));
    Assert.Contains("Set-NativeProcessArguments", browser, StringComparison.Ordinal);
    Assert.DoesNotContain(".ArgumentList.Add", browser, StringComparison.Ordinal);
  }

  [Fact]
  public void Stable_feed_installer_verifies_trust_hash_signature_and_required_executables()
  {
    string script = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "install-stable-update-feed.ps1"));

    Assert.Contains("#Requires -RunAsAdministrator", script, StringComparison.Ordinal);
    Assert.Contains("GetRSAPublicKey", script, StringComparison.Ordinal);
    Assert.Contains("VerifyData", script, StringComparison.Ordinal);
    Assert.Contains("Get-FileHash", script, StringComparison.Ordinal);
    Assert.Contains("TreadmillRunner.Gateway.exe", script, StringComparison.Ordinal);
    Assert.Contains("TreadmillRunner.Migrations.exe", script, StringComparison.Ordinal);
    Assert.Contains("Updates\\update-helper.ps1", script, StringComparison.Ordinal);
    Assert.Contains("Updates\\service-guardian.ps1", script, StringComparison.Ordinal);
    Assert.Contains("stable.manifest.json", script, StringComparison.Ordinal);
  }

  [Fact]
  public void Acceptance_fixture_selector_refuses_the_daily_ProgramData_feed()
  {
    string script = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "select-update-acceptance-fixture.ps1"));

    Assert.Contains("Acceptance fixtures cannot be selected into the daily ProgramData stable feed", script, StringComparison.Ordinal);
    Assert.DoesNotContain("[string] $DestinationFeed =", script, StringComparison.Ordinal);
  }

  [Fact]
  public void Physical_acceptance_preflight_is_get_only_and_cannot_issue_a_treadmill_command()
  {
    string script = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "physical-acceptance-preflight.ps1"));

    Assert.Contains("CommandPolicy = 'GET-only", script, StringComparison.Ordinal);
    Assert.Contains("Invoke-RestMethod -Method Get", script, StringComparison.Ordinal);
    Assert.Contains("Invoke-WebRequest -Method Get", script, StringComparison.Ordinal);
    Assert.DoesNotContain("-Method Post", script, StringComparison.OrdinalIgnoreCase);
    Assert.DoesNotContain("-Method Put", script, StringComparison.OrdinalIgnoreCase);
    Assert.DoesNotContain("-Method Delete", script, StringComparison.OrdinalIgnoreCase);
    Assert.DoesNotContain("/api/live/sessions/", script, StringComparison.OrdinalIgnoreCase);
    Assert.DoesNotContain("/api/devices/scan", script, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public void Recovery_acceptance_wrapper_targets_only_isolated_deterministic_tests()
  {
    string script = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "verify-recovery-acceptance.ps1"));

    Assert.Contains("UpdateManagerTests", script, StringComparison.Ordinal);
    Assert.Contains("SqliteRestoreServiceTests", script, StringComparison.Ordinal);
    Assert.Contains("PersistenceBackupRoundTripTests", script, StringComparison.Ordinal);
    Assert.Contains("ReleaseScriptContractTests", script, StringComparison.Ordinal);
    Assert.DoesNotContain("Start-ScheduledTask", script, StringComparison.OrdinalIgnoreCase);
    Assert.DoesNotContain("Restart-Service", script, StringComparison.OrdinalIgnoreCase);
    Assert.DoesNotContain("Stop-Service", script, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public async Task Acceptance_packager_runs_under_Windows_PowerShell_5_1()
  {
    string root = Path.Combine(Path.GetTempPath(), "TreadmillRunner.ReleaseScriptTests", Guid.NewGuid().ToString("N"));
    string publish = Path.Combine(root, "publish");
    string feed = Path.Combine(root, "feed");
    string certificate = Path.Combine(root, "acceptance.cer");
    Directory.CreateDirectory(Path.Combine(publish, "Updates"));
    await File.WriteAllTextAsync(Path.Combine(publish, "TreadmillRunner.Gateway.exe"), "gateway");
    await File.WriteAllTextAsync(Path.Combine(publish, "TreadmillRunner.Migrations.exe"), "migrations");
    await File.WriteAllTextAsync(Path.Combine(publish, "Updates", "update-helper.ps1"), "helper");
    await File.WriteAllTextAsync(Path.Combine(publish, "Updates", "service-guardian.ps1"), "guardian");
    try
    {
      var startInfo = new ProcessStartInfo
      {
        FileName = "powershell.exe",
        UseShellExecute = false,
        CreateNoWindow = true,
        RedirectStandardError = true,
        RedirectStandardOutput = true,
      };
      foreach (string argument in new[]
      {
        "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
        Path.Combine(ProjectRoot, "eng", "create-update-acceptance-feed.ps1"),
        "-PublishPath", publish,
        "-GoodVersion", "91.0.0",
        "-BrokenVersion", "91.0.1",
        "-FeedPath", feed,
        "-PublicCertificatePath", certificate,
      }) startInfo.ArgumentList.Add(argument);
      using Process process = Process.Start(startInfo)!;
      string output = await process.StandardOutput.ReadToEndAsync();
      string error = await process.StandardError.ReadToEndAsync();
      await process.WaitForExitAsync();
      Assert.True(process.ExitCode == 0, $"Windows PowerShell packaging failed: {error}{Environment.NewLine}{output}");
      using ZipArchive package = ZipFile.OpenRead(Path.Combine(feed, "treadmillrunner-91.0.0-win-x64.zip"));
      Assert.Contains(package.Entries, entry => entry.FullName == "Updates/update-helper.ps1");
      Assert.Contains(package.Entries, entry => entry.FullName == "Updates/service-guardian.ps1");
    }
    finally
    {
      if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
    }
  }

  [Fact]
  public void Privileged_helper_derives_trust_and_roots_from_protected_arguments()
  {
    string helper = File.ReadAllText(Path.Combine(ProjectRoot, "src", "TreadmillRunner.Gateway", "Updates", "update-helper.ps1"));
    string manager = File.ReadAllText(Path.Combine(ProjectRoot, "src", "TreadmillRunner.Gateway", "Updates", "UpdateManager.cs"));

    Assert.Contains("Join-Path $updaterRoot 'signing.cer'", helper, StringComparison.Ordinal);
    Assert.Contains("^[0-9a-f]{32}$", helper, StringComparison.Ordinal);
    Assert.Contains("The update transaction workspace already exists", helper, StringComparison.Ordinal);
    Assert.Contains("$currentVersionText = Split-Path -Leaf $currentReleasePath", helper, StringComparison.Ordinal);
    Assert.Contains("Test-Path -LiteralPath $previousImagePath -PathType Leaf", helper, StringComparison.Ordinal);
    Assert.DoesNotContain("$previousImagePath.Split(' ')[0]", helper, StringComparison.Ordinal);
    Assert.DoesNotContain("GetAssemblyName($currentExecutable)", helper, StringComparison.Ordinal);
    Assert.DoesNotContain("$plan.SigningCertificatePath", helper, StringComparison.Ordinal);
    Assert.DoesNotContain("SigningCertificatePath =", manager, StringComparison.Ordinal);
    Assert.Contains("service-maintenance.lock", helper, StringComparison.Ordinal);
    Assert.Contains("$maintenanceMarkerCreated", helper, StringComparison.Ordinal);
  }

  [Fact]
  public void Service_installer_supports_in_place_update_infrastructure_hardening()
  {
    string script = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "install-gateway-service.ps1"));

    Assert.Contains("RepairUpdateInfrastructureOnly", script, StringComparison.Ordinal);
    Assert.Contains("$updaterRoot 'signing.cer'", script, StringComparison.Ordinal);
    Assert.Contains("$readOnlyDirectory", script, StringComparison.Ordinal);
    Assert.Contains("$writableDirectory", script, StringComparison.Ordinal);
    Assert.Contains("-InstallRoot", script, StringComparison.Ordinal);
    Assert.Contains("-DataRoot", script, StringComparison.Ordinal);
    Assert.Contains("$dataProtectionKeyPath = Join-Path $resolvedDataRoot 'data\\keys'", script, StringComparison.Ordinal);
    Assert.Contains("Persistence__DataProtectionKeyPath=$dataProtectionKeyPath", script, StringComparison.Ordinal);
    Assert.Contains("$dataProtectionKeyPath, $backupRoot", script, StringComparison.Ordinal);
    Assert.Contains("Updates\\service-guardian.ps1", script, StringComparison.Ordinal);
    Assert.Contains("TreadmillRunnerGuardian", script, StringComparison.Ordinal);
    Assert.Contains("service-maintenance.lock", script, StringComparison.Ordinal);
    Assert.Contains("failureflag $serviceName 1", script, StringComparison.Ordinal);
  }

  [Fact]
  public void Service_guardian_recovers_only_outside_maintenance_and_bounds_its_log()
  {
    string script = File.ReadAllText(Path.Combine(ProjectRoot, "src", "TreadmillRunner.Gateway", "Updates", "service-guardian.ps1"));

    Assert.Contains("service-maintenance.lock", script, StringComparison.Ordinal);
    Assert.Contains("$maximumLogBytes = 1MB", script, StringComparison.Ordinal);
    Assert.Contains("service-guardian.previous.log", script, StringComparison.Ordinal);
    Assert.Contains("service-guardian-state.json", script, StringComparison.Ordinal);
    Assert.Contains("Microsoft-Windows-Services/Diagnostic", script, StringComparison.Ordinal);
    Assert.Contains("clientProcessId", script, StringComparison.Ordinal);
    Assert.Contains("parentProcessId", script, StringComparison.Ordinal);
    Assert.Contains("controlCode", script, StringComparison.Ordinal);
    Assert.Contains("Get-WinEvent", script, StringComparison.Ordinal);
    Assert.DoesNotContain("CommandLine", script, StringComparison.Ordinal);
    Assert.Contains("Start-Service -Name $ServiceName", script, StringComparison.Ordinal);
    Assert.Contains("recovery-complete", script, StringComparison.Ordinal);
    Assert.DoesNotContain("Stop-Service", script, StringComparison.Ordinal);
  }

  [Fact]
  public void Service_installer_enables_a_bounded_service_control_diagnostic_channel()
  {
    string script = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "install-gateway-service.ps1"));

    Assert.Contains("Microsoft-Windows-Services/Diagnostic", script, StringComparison.Ordinal);
    Assert.Contains("wevtutil.exe sl $serviceDiagnosticLog /ms:4194304 /q:true", script, StringComparison.Ordinal);
    Assert.Contains("wevtutil.exe sl $serviceDiagnosticLog /e:true /q:true", script, StringComparison.Ordinal);
  }

  [Fact]
  public async Task Service_guardian_honors_the_maintenance_marker_under_Windows_PowerShell_5_1()
  {
    string root = Path.Combine(Path.GetTempPath(), "TreadmillRunner.GuardianTests", Guid.NewGuid().ToString("N"));
    string updates = Path.Combine(root, "updates");
    Directory.CreateDirectory(updates);
    await File.WriteAllTextAsync(Path.Combine(updates, "service-maintenance.lock"), "test");
    try
    {
      var startInfo = new ProcessStartInfo
      {
        FileName = "powershell.exe",
        UseShellExecute = false,
        CreateNoWindow = true,
        RedirectStandardError = true,
        RedirectStandardOutput = true,
      };
      foreach (string argument in new[]
      {
        "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
        Path.Combine(ProjectRoot, "src", "TreadmillRunner.Gateway", "Updates", "service-guardian.ps1"),
        "-ServiceName", "TreadmillRunnerMissingTestService",
        "-DataRoot", root,
      }) startInfo.ArgumentList.Add(argument);
      using Process process = Process.Start(startInfo)!;
      string output = await process.StandardOutput.ReadToEndAsync();
      string error = await process.StandardError.ReadToEndAsync();
      await process.WaitForExitAsync();
      Assert.True(process.ExitCode == 0, $"Guardian did not honor maintenance: {error}{Environment.NewLine}{output}");
      Assert.False(File.Exists(Path.Combine(root, "logs", "service-guardian.log")));
    }
    finally
    {
      if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
    }
  }

  private static string FindProjectRoot()
  {
    DirectoryInfo? current = new(AppContext.BaseDirectory);
    while (current is not null)
    {
      if (File.Exists(Path.Combine(current.FullName, "TreadmillRunner.slnx"))) return current.FullName;
      current = current.Parent;
    }

    throw new InvalidOperationException("The TreadmillRunner project root could not be found.");
  }
}
