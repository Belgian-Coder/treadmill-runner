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
  [InlineData("run-deployment.ps1")]
  [InlineData("new-operator-access-secret.ps1")]
  [InlineData("capture-bluetooth-etw.ps1")]
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
  public void Bluetooth_etw_capture_uses_the_supported_multi_provider_file_contract()
  {
    string script = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "capture-bluetooth-etw.ps1"));

    Assert.Contains("'-pf', $providerFile", script, StringComparison.Ordinal);
    Assert.Contains("Set-Content -LiteralPath $providerFile -Encoding Ascii", script, StringComparison.Ordinal);
    Assert.Contains("Remove-Item -LiteralPath $providerFile", script, StringComparison.Ordinal);
    Assert.DoesNotContain("$arguments += @('-p'", script, StringComparison.Ordinal);
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
    Assert.Contains(".feed-transaction-", script, StringComparison.Ordinal);
    Assert.Contains("Write-FeedTransactionState", script, StringComparison.Ordinal);
    Assert.Contains("Recover-StaleFeedTransaction", script, StringComparison.Ordinal);
    Assert.Contains("MarkerCreated", script, StringComparison.Ordinal);
    Assert.Contains("ReplacementStarted", script, StringComparison.Ordinal);
    Assert.Contains("Published", script, StringComparison.Ordinal);
    Assert.Contains("The stable feed maintenance marker has no recoverable transaction state", script, StringComparison.Ordinal);
    Assert.Contains("previous stable feed pair was restored", script, StringComparison.Ordinal);
    Assert.Contains("post-replacement verification", script, StringComparison.Ordinal);
    Assert.Contains("AllowedHashes", script, StringComparison.Ordinal);
    Assert.Contains("previousPackageSha256", script, StringComparison.Ordinal);
    Assert.Contains("previousManifestSha256", script, StringComparison.Ordinal);
    Assert.Contains("function Copy-DurableFile", script, StringComparison.Ordinal);
    Assert.Contains("$destinationStream.Flush($true)", script, StringComparison.Ordinal);
    Assert.Contains("Copy-DurableFile -Source $backupPackage -Destination $destinationPackage", script, StringComparison.Ordinal);
    Assert.Contains("$validatedManifestSha256", script, StringComparison.Ordinal);
    Assert.Contains("The stable feed source changed after signature and package validation", script, StringComparison.Ordinal);
    int feedMutex = script.IndexOf("$maintenanceMutex =", StringComparison.Ordinal);
    int feedMarker = script.IndexOf("$markerBytes", StringComparison.Ordinal);
    int feedSnapshot = script.IndexOf("$hadPackage = Test-Path", StringComparison.Ordinal);
    Assert.True(feedMutex >= 0 && feedMarker > feedMutex && feedSnapshot > feedMarker,
      "feed rollback existence snapshots must be taken after the mutex and marker are held");
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
    Assert.Contains("preparation artifacts left before the first risky mutation", helper, StringComparison.Ordinal);
    Assert.Contains("$currentVersionText = Split-Path -Leaf $currentReleasePath", helper, StringComparison.Ordinal);
    Assert.Contains("function Get-ServiceExecutablePath", helper, StringComparison.Ordinal);
    Assert.Contains("function Restore-ServiceImageSafely", helper, StringComparison.Ordinal);
    Assert.Contains("TargetImagePath", helper, StringComparison.Ordinal);
    Assert.Contains("current gateway service image changed outside this update transaction", helper, StringComparison.Ordinal);
    Assert.Contains("The Windows Service ImagePath is not a single executable path", helper, StringComparison.Ordinal);
    Assert.DoesNotContain("$previousImagePath.Split(' ')[0]", helper, StringComparison.Ordinal);
    Assert.DoesNotContain("GetAssemblyName($currentExecutable)", helper, StringComparison.Ordinal);
    Assert.DoesNotContain("$plan.SigningCertificatePath", helper, StringComparison.Ordinal);
    Assert.DoesNotContain("SigningCertificatePath =", manager, StringComparison.Ordinal);
    Assert.Contains("service-maintenance.lock", helper, StringComparison.Ordinal);
    Assert.Contains("$maintenanceMarkerCreated", helper, StringComparison.Ordinal);
    Assert.Contains("InfrastructureRefresh", helper, StringComparison.Ordinal);
    Assert.Contains("RefreshExpectedHelperHash", helper, StringComparison.Ordinal);
    Assert.Contains("The pinned certificate or protected task actions changed", helper, StringComparison.Ordinal);
    Assert.Contains("Write-JournalPayload", helper, StringComparison.Ordinal);
    Assert.Contains("function Write-DurableTextFile", helper, StringComparison.Ordinal);
    Assert.Contains("$stream.Flush($true)", helper, StringComparison.Ordinal);
    Assert.Contains("Invoke-StaleUpdateRecovery", helper, StringComparison.Ordinal);
    Assert.Contains("parentProcessStartTicks", helper, StringComparison.Ordinal);
    Assert.Contains("before the child claimed transaction ownership", helper, StringComparison.Ordinal);
    Assert.Contains("Interrupted-update database sidecars remained after recovery", helper, StringComparison.Ordinal);
    Assert.Contains(".update-helper-$TransactionId.backup", helper, StringComparison.Ordinal);
    Assert.Contains("Start-Process -FilePath 'powershell.exe'", helper, StringComparison.Ordinal);
    Assert.Contains("RefreshReadyPath", helper, StringComparison.Ordinal);
    Assert.Contains("RefreshReadyToken", helper, StringComparison.Ordinal);
    Assert.Contains("RefreshCompletionPath", helper, StringComparison.Ordinal);
    Assert.Contains("Wait-RefreshCompletionAcknowledgement", helper, StringComparison.Ordinal);
    Assert.Contains("Wait-RefreshChildReady", helper, StringComparison.Ordinal);
    Assert.Contains("Wait-RefreshChildOwnership", helper, StringComparison.Ordinal);
    Assert.Contains("Wait-RefreshChildTerminal", helper, StringComparison.Ordinal);
    Assert.Contains("Wait-Process -Id $Process.Id -Timeout 10", helper, StringComparison.Ordinal);
    Assert.Contains("Read-RefreshTerminalState", helper, StringComparison.Ordinal);
    Assert.DoesNotContain("Wait-ParentProcessExit", helper, StringComparison.Ordinal);
    Assert.Contains("$refreshChildRolledBack", helper, StringComparison.Ordinal);
    Assert.Contains("$activationCompleted", helper, StringComparison.Ordinal);
    Assert.Contains("Complete-ActivatedParentCleanup", helper, StringComparison.Ordinal);
    Assert.Contains("The activation journal no longer records the expected Activated transaction", helper, StringComparison.Ordinal);
    Assert.Contains("The protected updater child did not complete within", helper, StringComparison.Ordinal);
    Assert.Contains("Stop-Process -Id $refreshChildProcess.Id", helper, StringComparison.Ordinal);
    Assert.Contains("if (-not $Process.HasExited)", helper, StringComparison.Ordinal);
    Assert.Contains("Reconcile once more outside the timed loop", helper, StringComparison.Ordinal);
    Assert.Contains("$finalChildState = Read-RefreshTerminalState", helper, StringComparison.Ordinal);
    Assert.Contains("if ($refreshChildRolledBack -and $rollbackCompleted) { throw }", helper, StringComparison.Ordinal);
    int finalChildExitCheck = helper.LastIndexOf("if (-not $refreshChildProcess.HasExited)", StringComparison.Ordinal);
    int finalChildJournalRead = helper.LastIndexOf("$finalChildState = Read-RefreshTerminalState", StringComparison.Ordinal);
    int parentRollbackMutation = helper.LastIndexOf("if ($serviceMutationStarted -or $newReleasePromoted)", StringComparison.Ordinal);
    Assert.True(finalChildExitCheck >= 0 && finalChildJournalRead > finalChildExitCheck && parentRollbackMutation > finalChildJournalRead);
    Assert.Contains("recovery artifacts were preserved", helper, StringComparison.Ordinal);
    Assert.Contains("parent rollback was not started", helper, StringComparison.Ordinal);
    Assert.Contains("Copy-DurableFile -Source $helperBackupPath -Destination $helperTarget", helper, StringComparison.Ordinal);
    Assert.Contains("Copy-DurableFile -Source $guardianBackupPath -Destination $guardianTarget", helper, StringComparison.Ordinal);
    Assert.Contains("The parent could not reacquire the maintenance lock for rollback", helper, StringComparison.Ordinal);
    Assert.Contains("New-MaintenanceMutex", helper, StringComparison.Ordinal);
    Assert.Contains("Remove-FailedRelease", helper, StringComparison.Ordinal);
    Assert.Contains("$terminalCleanupAllowed", helper, StringComparison.Ordinal);
    Assert.Contains("if ($activationJournaled)", helper, StringComparison.Ordinal);
    Assert.Contains("must never turn success into rollback", helper, StringComparison.Ordinal);
    Assert.Contains("updatePrincipalRunLevel", helper, StringComparison.Ordinal);
    Assert.Contains("RefreshOwnershipPath", helper, StringComparison.Ordinal);
    Assert.Contains("RefreshOwnershipToken", helper, StringComparison.Ordinal);
    Assert.Contains("RefreshIncomingPath", helper, StringComparison.Ordinal);
    Assert.Contains("transactionStarted", helper, StringComparison.Ordinal);
    Assert.Contains("removed only the transaction-owned incoming workspace", helper, StringComparison.Ordinal);
    Assert.Contains("$databaseMutationPath", helper, StringComparison.Ordinal);
    Assert.Contains("$helperBackupPath", helper, StringComparison.Ordinal);
    Assert.Contains("$guardianBackupPath", helper, StringComparison.Ordinal);
    int expectedHash = helper.IndexOf("$helperExpectedHash = Get-FileSha256", StringComparison.Ordinal);
    int durablePrecondition = helper.IndexOf("Write-DurableTextFile -Path $preconditionPath", StringComparison.Ordinal);
    Assert.True(expectedHash >= 0 && durablePrecondition > expectedHash);
    int durableActivating = helper.LastIndexOf("Write-Journal -Path $journalPath -State 'Activating'", StringComparison.Ordinal);
    int riskyServiceStop = helper.LastIndexOf("Stop-Service -Name $serviceName -Force", StringComparison.Ordinal);
    Assert.True(durableActivating >= 0 && riskyServiceStop > durableActivating);
    Assert.Contains("$maintenanceMutex.ReleaseMutex()", helper, StringComparison.Ordinal);
  }

  [Fact]
  public void Service_installer_creates_the_maintenance_marker_inside_its_cleanup_scope()
  {
    string script = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "install-gateway-service.ps1"));
    int tryStart = script.IndexOf("try\n{", StringComparison.Ordinal);
    if (tryStart < 0) tryStart = script.IndexOf("try {", StringComparison.Ordinal);
    int markerWrite = script.IndexOf("$maintenanceMarkerPath,", StringComparison.Ordinal);
    int finallyStart = script.IndexOf("finally", markerWrite, StringComparison.Ordinal);
    Assert.True(tryStart >= 0 && markerWrite > tryStart && finallyStart > markerWrite);
    Assert.Contains("targetReleaseOwned", script, StringComparison.Ordinal);
    Assert.Contains("serviceInstallStarted", script, StringComparison.Ordinal);
    Assert.Contains("Remove-Item -LiteralPath $targetRelease -Recurse -Force", script, StringComparison.Ordinal);
  }

  [Fact]
  public async Task Protected_helper_rejects_refresh_paths_outside_fixed_roots_before_mutation()
  {
    string root = Path.Combine(Path.GetTempPath(), "TreadmillRunner.HelperContract", Guid.NewGuid().ToString("N"));
    string install = Path.Combine(root, "install");
    string data = Path.Combine(root, "data");
    Directory.CreateDirectory(Path.Combine(install, "updater"));
    Directory.CreateDirectory(Path.Combine(install, "releases", "1.0.0"));
    Directory.CreateDirectory(Path.Combine(data, "updates", "plans"));
    Directory.CreateDirectory(Path.Combine(data, "backups"));
    string sourceHelper = Path.Combine(ProjectRoot, "src", "TreadmillRunner.Gateway", "Updates", "update-helper.ps1");
    string helper = Path.Combine(install, "updater", "update-helper.ps1");
    File.Copy(sourceHelper, helper);
    string tx = new string('a', 32);
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
      "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", helper,
      "-InfrastructureRefresh", "-InstallRoot", install, "-DataRoot", data,
      "-PlanPath", Path.Combine(data, "updates", "plans", "pending-activation.json"),
      "-RefreshTransactionId", tx, "-RefreshExpectedVersion", "2.0.0",
      "-RefreshNewReleasePath", Path.Combine(root, "outside-release"),
      "-RefreshIncomingPath", Path.Combine(install, "releases", $".incoming-{tx}"),
      "-RefreshPreviousImagePath", Path.Combine(install, "releases", "1.0.0", "TreadmillRunner.Gateway.exe"),
      "-RefreshDatabaseBackupPath", Path.Combine(data, "backups", $"pre-update-{tx}.db"),
      "-RefreshJournalPath", Path.Combine(data, "updates", "plans", $"transaction-{tx}.json"),
      "-RefreshMaintenanceMarkerPath", Path.Combine(data, "updates", "service-maintenance.lock"),
      "-RefreshHelperPath", Path.Combine(install, "updater", "update-helper.ps1"),
      "-RefreshGuardianPath", Path.Combine(install, "updater", "service-guardian.ps1"),
      "-RefreshReadyPath", Path.Combine(install, "updater", $".update-ready-{tx}.token"),
      "-RefreshReadyToken", "contract-token",
      "-RefreshStartPath", Path.Combine(install, "updater", $".update-start-{tx}.token"),
      "-RefreshStartToken", "contract-start-token",
      "-RefreshCompletionPath", Path.Combine(install, "updater", $".update-completion-{tx}.token"),
      "-RefreshCompletionToken", "contract-completion-token",
      "-RefreshOwnershipPath", Path.Combine(install, "updater", $".update-ownership-{tx}.token"),
      "-RefreshOwnershipToken", "contract-ownership-token",
      "-RefreshDatabaseMutationPath", Path.Combine(install, "updater", $".update-database-{tx}.token"),
      "-RefreshDatabaseMutationToken", "contract-database-token",
      "-RefreshExpectedHelperHash", new string('0', 64),
      "-RefreshExpectedGuardianHash", new string('0', 64),
      "-RefreshPreconditionPath", Path.Combine(install, "updater", $".update-preconditions-{tx}.json"),
    }) startInfo.ArgumentList.Add(argument);
    try
    {
      using Process process = Process.Start(startInfo)!;
      string output = await process.StandardOutput.ReadToEndAsync();
      string error = await process.StandardError.ReadToEndAsync();
      await process.WaitForExitAsync();
      Assert.NotEqual(0, process.ExitCode);
      Assert.Contains("New release path is outside its fixed update path contract", $"{output}{error}", StringComparison.Ordinal);
      Assert.False(File.Exists(Path.Combine(data, "updates", "service-maintenance.lock")));
      Assert.False(File.Exists(Path.Combine(install, "updater", $".update-ready-{tx}.token")));
      Assert.False(File.Exists(Path.Combine(install, "updater", $".update-ownership-{tx}.token")));
    }
    finally
    {
      if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
    }
  }

  [Fact]
  public void Deployment_coordinator_requires_exact_release_commit_and_explicit_idle_activation()
  {
    string script = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "run-deployment.ps1"));
    Assert.Contains("ExpectedVersion", script, StringComparison.Ordinal);
    Assert.Contains("ExpectedCommit", script, StringComparison.Ordinal);
    Assert.Contains("ExpectedRelease", script, StringComparison.Ordinal);
    Assert.Contains("Invoke-GhJson", script, StringComparison.Ordinal);
    Assert.Contains("'release', 'view'", script, StringComparison.Ordinal);
    Assert.Contains("gh api", script, StringComparison.Ordinal);
    Assert.Contains("SHA256SUMS.txt", script, StringComparison.Ordinal);
    Assert.Contains("install-stable-update-feed.ps1", script, StringComparison.Ordinal);
    Assert.Contains("physical-acceptance-preflight.ps1", script, StringComparison.Ordinal);
    Assert.Contains("/api/updates/check", script, StringComparison.Ordinal);
    Assert.Contains("/api/updates/stage", script, StringComparison.Ordinal);
    Assert.Contains("/api/updates/activate", script, StringComparison.Ordinal);
    Assert.Contains("Confirmation ACTIVATE", script, StringComparison.Ordinal);
    Assert.Contains("StatusCode -eq 204", script, StringComparison.Ordinal);
    Assert.Contains("StatusCode -ne 200", script, StringComparison.Ordinal);
    Assert.Contains("$text -eq '4'", script, StringComparison.Ordinal);
    Assert.Contains("$text -eq '5'", script, StringComparison.Ordinal);
    Assert.Contains("$text -eq '6'", script, StringComparison.Ordinal);
    Assert.Contains("$text -eq '7'", script, StringComparison.Ordinal);
    Assert.Contains("$text -eq 'Interrupted'", script, StringComparison.Ordinal);
    Assert.Contains("$text -eq 'Faulted'", script, StringComparison.Ordinal);
    Assert.Contains("Convert-TerminalSessionState -Value $history.state", script, StringComparison.Ordinal);
    Assert.Contains("Restart-Service -Name 'TreadmillRunnerGateway'", script, StringComparison.Ordinal);
    Assert.Contains("/api/history/$sessionId", script, StringComparison.Ordinal);
    Assert.Contains("The live session $sessionId and durable history disagree", script, StringComparison.Ordinal);
    Assert.Contains("/api/system/version", script, StringComparison.Ordinal);
    Assert.Contains("/api/planning/profiles", script, StringComparison.Ordinal);
    Assert.Contains("/api/history?profileId=", script, StringComparison.Ordinal);
    Assert.Contains("take=5000", script, StringComparison.Ordinal);
    Assert.Contains("Expand-JsonArray", script, StringComparison.Ordinal);
    Assert.Contains("Profile $($profile.id) payload changed", script, StringComparison.Ordinal);
    Assert.Contains("History item $historyId", script, StringComparison.Ordinal);
    Assert.Contains("stagedVersion -ne $ExpectedVersion", script, StringComparison.Ordinal);
    Assert.Contains("schemaVersion -ne 1", script, StringComparison.Ordinal);
    Assert.Contains("manifest.channel -cne 'stable'", script, StringComparison.Ordinal);
    Assert.Contains("Get-CurrentInstalledRelease", script, StringComparison.Ordinal);
    Assert.Contains("Expand-VerifiedRepairSource", script, StringComparison.Ordinal);
    Assert.Contains("Repair-ProtectedInfrastructure", script, StringComparison.Ordinal);
    Assert.Contains("protected-infrastructure-repair", script, StringComparison.Ordinal);
    Assert.Contains("-RepairUpdateInfrastructureOnly", script, StringComparison.Ordinal);
    Assert.Contains("four installer-required entries", script, StringComparison.Ordinal);
    Assert.Contains("if (-not $DryRun)", script, StringComparison.Ordinal);
    Assert.Contains("Normalize a verified terminal session before the GET-only physical", script, StringComparison.Ordinal);
    int preflight = script.IndexOf("# This is a GET-only, no-command preflight", StringComparison.Ordinal);
    int preflightIdle = script.IndexOf("Normalize a verified terminal session before the GET-only physical", StringComparison.Ordinal);
    Assert.True(preflightIdle >= 0 && preflight > preflightIdle,
      "mutating deployment modes must normalize a terminal session before physical preflight");
  }

  [Fact]
  public void Update_manager_retention_is_terminal_state_only_and_conservative()
  {
    string manager = File.ReadAllText(Path.Combine(ProjectRoot, "src", "TreadmillRunner.Gateway", "Updates", "UpdateManager.cs"));
    string endpoints = File.ReadAllText(Path.Combine(ProjectRoot, "src", "TreadmillRunner.Gateway", "Updates", "UpdateEndpoints.cs"));
    Assert.Contains("MaximumRetainedTerminalTransactions = 5", manager, StringComparison.Ordinal);
    Assert.Contains("journal.State is \"Activated\" or \"RolledBack\"", manager, StringComparison.Ordinal);
    Assert.Contains("journal.State is not (\"Activating\" or \"Activated\" or \"RolledBack\" or \"RollbackFailed\")", manager, StringComparison.Ordinal);
    Assert.Contains("pending-activation.json", manager, StringComparison.Ordinal);
    Assert.Contains("ScheduledTaskName must be TreadmillRunnerUpdate", manager, StringComparison.Ordinal);
    Assert.Contains("ContainsReparsePoint", manager, StringComparison.Ordinal);
    Assert.Contains("Staged is { } staged", manager, StringComparison.Ordinal);
    Assert.Contains("Directory.Delete(stagePath, recursive: true)", manager, StringComparison.Ordinal);
    Assert.Contains("bool taskStarted = false", manager, StringComparison.Ordinal);
    Assert.Contains("catch (Exception) when (taskStarted)", manager, StringComparison.Ordinal);
    Assert.Contains("launcher result was indeterminate", manager, StringComparison.Ordinal);
    Assert.Contains("Task.Run", manager, StringComparison.Ordinal);
    Assert.Contains("ActivateUnderMaintenanceMutex", manager, StringComparison.Ordinal);
    Assert.Contains("using MaintenanceMutexLease maintenanceLease = AcquireMaintenanceMutex()", manager, StringComparison.Ordinal);
    Assert.Contains("EnsureReleaseWasNotRejected(staged.Version)", manager, StringComparison.Ordinal);
    Assert.Contains("ReadTransactionJournalsForActivation", manager, StringComparison.Ordinal);
    Assert.Contains("Update transaction history is unreadable or ambiguous", manager, StringComparison.Ordinal);
    Assert.Contains("The update maintenance lock could not be acquired", manager, StringComparison.Ordinal);
    Assert.Contains("The signed update task was queued", manager, StringComparison.Ordinal);
    Assert.Contains("if (!activationAccepted) await live.CancelMaintenanceAsync", endpoints, StringComparison.Ordinal);
  }

  [Fact]
  public void Service_installer_supports_in_place_update_infrastructure_hardening()
  {
    string script = File.ReadAllText(Path.Combine(ProjectRoot, "eng", "install-gateway-service.ps1"));

    Assert.Contains("RepairUpdateInfrastructureOnly", script, StringComparison.Ordinal);
    Assert.Contains("$taskName, $guardianTaskName", script, StringComparison.Ordinal);
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
    Assert.Contains("pending activation or refresh workspace", script, StringComparison.Ordinal);
    Assert.Contains("Global\\TreadmillRunnerGateway.Maintenance", script, StringComparison.Ordinal);
    Assert.Contains("if ([string]$state.phase -eq 'Committed')", script, StringComparison.Ordinal);
    Assert.Contains("processStartTimeUtc", script, StringComparison.Ordinal);
    Assert.Contains("processPath", script, StringComparison.Ordinal);
    Assert.Contains("Get-ExistingGatewayService", script, StringComparison.Ordinal);
    Assert.Contains("Get-ExistingRootScheduledTask", script, StringComparison.Ordinal);
    Assert.Contains("does not own the existing gateway service image", script, StringComparison.Ordinal);
    Assert.Contains("protectedFiles", script, StringComparison.Ordinal);
    Assert.Contains("serviceEnvironmentCaptured", script, StringComparison.Ordinal);
    Assert.Contains("directoryAclStates", script, StringComparison.Ordinal);
    Assert.Contains("Restore-InstallerMutableInfrastructure", script, StringComparison.Ordinal);
    Assert.Contains("Set-PostCommitOperationalInfrastructure", script, StringComparison.Ordinal);
    Assert.Contains("backupSha256", script, StringComparison.Ordinal);
    Assert.Contains("Initialize-InstallerProtectedFileState", script, StringComparison.Ordinal);
    Assert.Contains("record.changed", script, StringComparison.Ordinal);
    Assert.Contains("migrationBackupPath", script, StringComparison.Ordinal);
    Assert.Contains("migrationBackupSha256", script, StringComparison.Ordinal);
    Assert.Contains("restoredMigrationPhases", script, StringComparison.Ordinal);
    Assert.Contains("migration backup is missing and the database restore is not durably verified", script, StringComparison.Ordinal);
    Assert.Contains("MigrationCommitted", script, StringComparison.Ordinal);
    Assert.Contains("Commit-InstallerMigrationBackup", script, StringComparison.Ordinal);
    int durableCommit = script.LastIndexOf("Write-InstallerState -Phase 'Committed'", StringComparison.Ordinal);
    int migrationSnapshotCleanup = script.LastIndexOf("Commit-InstallerMigrationBackup", StringComparison.Ordinal);
    Assert.True(durableCommit >= 0 && migrationSnapshotCleanup > durableCommit);
    Assert.Contains("Register-ScheduledTask -TaskName $Name -Xml $PreviousXml", script, StringComparison.Ordinal);
    Assert.Contains("Unregister-ScheduledTask -TaskName $Name", script, StringComparison.Ordinal);
    Assert.Contains("updateTaskRegistrationCompleted", script, StringComparison.Ordinal);
    Assert.Contains("guardianTaskRegistrationCompleted", script, StringComparison.Ordinal);
    Assert.Contains("updateTaskRegisteredArguments", script, StringComparison.Ordinal);
    Assert.Contains("guardianTaskRegisteredArguments", script, StringComparison.Ordinal);
    Assert.Contains("Test-InstallerTaskMatchesRegistration", script, StringComparison.Ordinal);
    Assert.Contains("is neither the prior task nor the exact transaction registration", script, StringComparison.Ordinal);
    Assert.Contains("Recovery is idempotent after the compensating mutation itself completed", script, StringComparison.Ordinal);
    Assert.Contains("$RegistrationCompleted -and", script, StringComparison.Ordinal);
    Assert.Contains("Restore-InstallerServiceImageSafely", script, StringComparison.Ordinal);
    Assert.Contains("current gateway service image changed outside this installer transaction", script, StringComparison.Ordinal);
    Assert.Contains("FileOptions]::WriteThrough", script, StringComparison.Ordinal);
    Assert.Contains("The installer maintenance marker is no longer owned by this transaction", script, StringComparison.Ordinal);
    int serviceMutationRecord = script.IndexOf("Write-InstallerState -Phase 'ServiceMutationStarted'", StringComparison.Ordinal);
    int serviceStop = script.IndexOf("Stop-Service -Name $serviceName -Force", serviceMutationRecord, StringComparison.Ordinal);
    Assert.True(serviceMutationRecord >= 0 && serviceStop > serviceMutationRecord);
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
    Assert.Contains("Global\\TreadmillRunnerGateway.Maintenance", script, StringComparison.Ordinal);
    Assert.Contains("Wait-MaintenanceMutex", script, StringComparison.Ordinal);
    Assert.Contains("AbandonedMutexException", script, StringComparison.Ordinal);
    Assert.DoesNotContain("Stop-Service", script, StringComparison.Ordinal);

    const string markerCheck = "Test-Path -LiteralPath $maintenanceMarker -PathType Leaf";
    int stoppedServiceCheck = script.IndexOf("if ($service.State -ne 'Stopped')", StringComparison.Ordinal);
    int secondMarkerCheck = script.IndexOf(markerCheck, stoppedServiceCheck, StringComparison.Ordinal);
    int thirdMarkerCheck = script.IndexOf(markerCheck, secondMarkerCheck + markerCheck.Length, StringComparison.Ordinal);
    int recoveryStart = script.IndexOf("Write-GuardianLog -EventName 'recovery-start'", StringComparison.Ordinal);
    Assert.True(stoppedServiceCheck >= 0 && secondMarkerCheck > stoppedServiceCheck);
    Assert.True(thirdMarkerCheck > secondMarkerCheck && thirdMarkerCheck < recoveryStart);
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
