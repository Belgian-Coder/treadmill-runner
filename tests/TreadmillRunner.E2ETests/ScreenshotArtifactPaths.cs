namespace TreadmillRunner.E2ETests;

internal static class ScreenshotArtifactPaths
{
  private const string UpdateShowcaseEnvironmentVariable = "TREADMILLRUNNER_UPDATE_SHOWCASE";

  internal static string ShowcaseDirectory(string projectRoot)
  {
    ArgumentException.ThrowIfNullOrWhiteSpace(projectRoot);
    string directory = string.Equals(
      Environment.GetEnvironmentVariable(UpdateShowcaseEnvironmentVariable),
      "1",
      StringComparison.Ordinal)
        ? Path.Combine(projectRoot, "screenshots", "showcase")
        : Path.Combine(projectRoot, "output", "playwright", "showcase");
    Directory.CreateDirectory(directory);
    return directory;
  }
}
