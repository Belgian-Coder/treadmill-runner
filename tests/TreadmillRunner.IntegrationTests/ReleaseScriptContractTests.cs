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
  [InlineData("publish-release.ps1")]
  [InlineData("Install-TreadmillRunner.ps1")]
  [InlineData("new-installer-bundle.ps1")]
  [InlineData("create-github-release.ps1")]
  [InlineData("test.ps1")]
  [InlineData("playwright.ps1")]
  [InlineData("validate-connectiq.ps1")]
  [InlineData("verify-change.ps1")]
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

    Assert.Contains("belgian-coder/treadmill-runner", release, StringComparison.Ordinal);
    Assert.Contains("gh auth status", release, StringComparison.Ordinal);
    Assert.Contains("stable.manifest.json", release, StringComparison.Ordinal);
    Assert.Contains("offline-update.zip", release, StringComparison.Ordinal);
    Assert.Contains("--draft", release, StringComparison.Ordinal);
    Assert.Contains("--verify-tag", release, StringComparison.Ordinal);
    Assert.Contains("git tag -a $tag", release, StringComparison.Ordinal);
    Assert.Contains("git fetch origin \"refs/tags/${tag}:refs/tags/${tag}\"", release, StringComparison.Ordinal);
    Assert.Contains("git rev-list -n 1", release, StringComparison.Ordinal);
    Assert.Contains("Tags are never moved", release, StringComparison.Ordinal);
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

    Assert.Contains("git -C $projectRoot diff --binary HEAD -- src Directory.Build.props", script, StringComparison.Ordinal);
    Assert.Contains("git -C $projectRoot ls-files --others --exclude-standard -- src Directory.Build.props", script, StringComparison.Ordinal);
    Assert.Contains("-p:TreadmillRunnerBuildId=$buildId", script, StringComparison.Ordinal);
    Assert.Contains("-p:InformationalVersion=\"$Version+$buildId\"", script, StringComparison.Ordinal);
    Assert.Contains("build-metadata.json", script, StringComparison.Ordinal);
    Assert.Contains("sourceRevision = $headRevision", script, StringComparison.Ordinal);
    Assert.Contains("buildId = $buildId", script, StringComparison.Ordinal);
    Assert.Contains("TreadmillRunnerBuildId", buildProps, StringComparison.Ordinal);
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
