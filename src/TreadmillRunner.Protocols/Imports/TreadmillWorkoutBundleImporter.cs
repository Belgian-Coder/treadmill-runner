using System.Globalization;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.Protocols.Imports;

public enum WorkoutSetSelectionStrategy
{
  Default,
  PreferHeartRate,
  PreferFixed,
  PreferOmegaRecovery,
}

public sealed record TreadmillWorkoutBundleVariant(
  string CanonicalSlot,
  string SessionId,
  string Variant,
  int Week,
  int Session,
  string Title,
  string ControlMode,
  string SelectionRule,
  string SourcePath,
  WorkoutDefinition Definition);

public sealed record TreadmillWorkoutBundleSlot(
  string CanonicalSlot,
  int Week,
  int Session,
  IReadOnlyList<TreadmillWorkoutBundleVariant> Variants);

public sealed record TreadmillWorkoutBundle(
  string PlanName,
  string Category,
  string ToolVersion,
  IReadOnlyList<TreadmillWorkoutBundleSlot> Slots,
  IReadOnlyList<string> Warnings)
{
  public IReadOnlyList<TreadmillWorkoutBundleVariant> Select(WorkoutSetSelectionStrategy strategy) =>
    Slots.Select(slot => Select(slot, strategy)).ToArray();

  private static TreadmillWorkoutBundleVariant Select(
    TreadmillWorkoutBundleSlot slot,
    WorkoutSetSelectionStrategy strategy)
  {
    string? preferred = strategy switch
    {
      WorkoutSetSelectionStrategy.PreferHeartRate => "hr-alternative",
      WorkoutSetSelectionStrategy.PreferFixed => "fixed-fallback",
      WorkoutSetSelectionStrategy.PreferOmegaRecovery => "omega-recovery-incline",
      _ => null,
    };
    return (preferred is null
        ? null
        : slot.Variants.FirstOrDefault(variant => string.Equals(variant.Variant, preferred, StringComparison.Ordinal)))
      ?? slot.Variants.Single(variant => string.Equals(variant.Variant, "primary", StringComparison.Ordinal));
  }
}

public sealed class TreadmillWorkoutBundleImporter
{
  public const int MaximumArchiveBytes = 64 * 1024 * 1024;
  public const long MaximumExpandedBytes = 256L * 1024 * 1024;
  public const int MaximumEntries = 5_000;
  private const long MaximumEntryBytes = 10L * 1024 * 1024;
  private const int MaximumExpansionRatio = 100;
  private const int MaximumCanonicalSlotLength = 64;
  private const int MaximumSessionIdLength = 100;
  private const int MaximumControlModeLength = 64;
  private const int MaximumSelectionRuleLength = 256;
  private const string ManifestName = "manifest.json";
  private const string IndexName = "workout_index.csv";
  private const string OmegaPrefix = "treadmill/horizon-omega-z-dark/sessions/";
  private const string CompatibilityProfile = "treadmill-multi-device-bundle-v4";
  private const string OmegaProfile = "horizon-omega-z-dark-2023-ftms";
  private static readonly string[] RequiredIndexColumns =
  [
    "canonical_slot", "session_id", "variant", "intended_control_mode", "week", "session",
    "title", "horizon_omega_z_file", "perform_exactly_one_variant", "alternative_of", "selection_rule",
  ];

  private readonly QDomyosWorkoutXmlImporter _xmlImporter = new();

  public async ValueTask<TreadmillWorkoutBundle> ImportAsync(
    Stream source,
    string fileName,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(source);
    if (!string.Equals(Path.GetExtension(fileName), ".zip", StringComparison.OrdinalIgnoreCase))
      throw new WorkoutImportException("Choose a treadmill-workout v4 ZIP bundle.");

    byte[] archiveBytes = await ReadArchiveAsync(source, cancellationToken);
    using var archiveStream = new MemoryStream(archiveBytes, writable: false);
    using var archive = new ZipArchive(archiveStream, ZipArchiveMode.Read, leaveOpen: false);
    Dictionary<string, ZipArchiveEntry> entries = ValidateEntries(archive);
    Manifest manifest = await ReadManifestAsync(entries, cancellationToken);
    await VerifyArtifactsAsync(entries, manifest, cancellationToken);
    IReadOnlyList<Dictionary<string, string>> rows = await ReadIndexAsync(entries, cancellationToken);
    (string planName, string category) = await ReadPlanIdentityAsync(entries, fileName, cancellationToken);

    var slots = new List<TreadmillWorkoutBundleSlot>();
    var sessionIds = new HashSet<string>(StringComparer.Ordinal);
    foreach (IGrouping<string, Dictionary<string, string>> group in rows
      .GroupBy(row => row["canonical_slot"], StringComparer.Ordinal)
      .OrderBy(group => ParsePositive(group.First()["week"], "week"))
      .ThenBy(group => ParsePositive(group.First()["session"], "session")))
    {
      Dictionary<string, string>[] slotRows = group.ToArray();
      if (slotRows.Count(row => string.Equals(row["variant"], "primary", StringComparison.Ordinal)) != 1)
        throw new WorkoutImportException($"Slot {group.Key} must contain exactly one primary variant.");
      if (slotRows.Select(row => row["variant"]).Distinct(StringComparer.Ordinal).Count() != slotRows.Length)
        throw new WorkoutImportException($"Slot {group.Key} contains a duplicate variant.");
      int slotWeek = ParsePositive(slotRows[0]["week"], "week");
      int slotSession = ParsePositive(slotRows[0]["session"], "session");

      var variants = new List<TreadmillWorkoutBundleVariant>(slotRows.Length);
      foreach (Dictionary<string, string> row in slotRows)
      {
        ValidateRow(row, group.Key);
        if (ParsePositive(row["week"], "week") != slotWeek || ParsePositive(row["session"], "session") != slotSession)
          throw new WorkoutImportException($"All variants in slot {group.Key} must use the same week and session.");
        if (!sessionIds.Add(row["session_id"]))
          throw new WorkoutImportException($"The workout index contains duplicate session ID '{row["session_id"]}'.");
        string path = row["horizon_omega_z_file"];
        if (!entries.TryGetValue(path, out ZipArchiveEntry? xmlEntry))
          throw new WorkoutImportException($"Indexed Omega workout is missing: {path}.");
        byte[] xml = await ReadEntryAsync(xmlEntry, MaximumEntryBytes, cancellationToken);
        await using var xmlStream = new MemoryStream(xml, writable: false);
        WorkoutImportResult imported = await _xmlImporter.ImportBundleV4Async(xmlStream, Path.GetFileName(path), cancellationToken);
        string title = $"{group.Key} · {NormalizeTitle(row["title"])}";
        var definition = new WorkoutDefinition(
          imported.Definition.SchemaVersion,
          title,
          $"Imported from treadmill-workout {manifest.ToolVersion}; {row["selection_rule"]}",
          imported.Definition.Blocks);
        variants.Add(new TreadmillWorkoutBundleVariant(
          group.Key,
          row["session_id"],
          row["variant"],
          ParsePositive(row["week"], "week"),
          ParsePositive(row["session"], "session"),
          title,
          row["intended_control_mode"],
          row["selection_rule"],
          path,
          definition));
      }
      slots.Add(new TreadmillWorkoutBundleSlot(
        group.Key,
        slotWeek,
        slotSession,
        variants));
    }
    if (slots.Count == 0) throw new WorkoutImportException("The bundle contains no indexed workouts.");

    int alternativeCount = slots.Sum(slot => slot.Variants.Count - 1);
    string[] warnings = alternativeCount == 0
      ? []
      : [$"{alternativeCount} alternative variants are available. The training plan will contain exactly one variant per slot."];
    return new TreadmillWorkoutBundle(planName, category, manifest.ToolVersion, slots, warnings);
  }

  private static async Task<byte[]> ReadArchiveAsync(Stream source, CancellationToken cancellationToken)
  {
    using var buffer = new MemoryStream();
    var block = new byte[81920];
    while (true)
    {
      int read = await source.ReadAsync(block, cancellationToken);
      if (read == 0) break;
      if (buffer.Length + read > MaximumArchiveBytes)
        throw new WorkoutImportException("The generated workout-set ZIP exceeds the 64 MB limit.");
      await buffer.WriteAsync(block.AsMemory(0, read), cancellationToken);
    }
    if (buffer.Length == 0) throw new WorkoutImportException("The generated workout-set ZIP is empty.");
    return buffer.ToArray();
  }

  private static Dictionary<string, ZipArchiveEntry> ValidateEntries(ZipArchive archive)
  {
    if (archive.Entries.Count is 0 or > MaximumEntries)
      throw new WorkoutImportException($"The ZIP must contain between 1 and {MaximumEntries} entries.");
    var entries = new Dictionary<string, ZipArchiveEntry>(StringComparer.Ordinal);
    var caseInsensitive = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    long total = 0;
    foreach (ZipArchiveEntry entry in archive.Entries)
    {
      string name = entry.FullName;
      if (name.EndsWith("/", StringComparison.Ordinal)) continue;
      if (!IsSafePath(name) || !caseInsensitive.Add(name) || !entries.TryAdd(name, entry))
        throw new WorkoutImportException($"The ZIP contains an unsafe or duplicate path: {name}.");
      int unixMode = (entry.ExternalAttributes >> 16) & 0xF000;
      if (unixMode == 0xA000) throw new WorkoutImportException($"Symbolic links are not allowed in a workout bundle: {name}.");
      if (entry.Length < 0 || entry.Length > MaximumEntryBytes)
        throw new WorkoutImportException($"ZIP entry {name} exceeds the 10 MB per-file limit.");
      if (entry.CompressedLength > 0 && entry.Length / Math.Max(1, entry.CompressedLength) > MaximumExpansionRatio)
        throw new WorkoutImportException($"ZIP entry {name} exceeds the safe expansion ratio.");
      total = checked(total + entry.Length);
      if (total > MaximumExpandedBytes)
        throw new WorkoutImportException("The ZIP expands beyond the 256 MB limit.");
    }
    return entries;
  }

  private static bool IsSafePath(string name)
  {
    if (string.IsNullOrWhiteSpace(name) || name[0] == '/' || name.Contains('\\') || name.Contains(':')) return false;
    string[] parts = name.Split('/');
    return parts.All(part => part.Length > 0 && part is not "." and not "..");
  }

  private static async Task<Manifest> ReadManifestAsync(
    IReadOnlyDictionary<string, ZipArchiveEntry> entries,
    CancellationToken cancellationToken)
  {
    if (!entries.TryGetValue(ManifestName, out ZipArchiveEntry? entry))
      throw new WorkoutImportException("The bundle is missing manifest.json.");
    byte[] bytes = await ReadEntryAsync(entry, MaximumEntryBytes, cancellationToken);
    try
    {
      using JsonDocument document = JsonDocument.Parse(bytes, new JsonDocumentOptions { MaxDepth = 16 });
      JsonElement root = document.RootElement;
      if (root.ValueKind != JsonValueKind.Object ||
          root.GetProperty("format_version").GetInt32() != 2 ||
          !string.Equals(root.GetProperty("tool").GetString(), "treadmill-workout", StringComparison.Ordinal) ||
          !string.Equals(root.GetProperty("compatibility_profile").GetString(), CompatibilityProfile, StringComparison.Ordinal))
        throw new WorkoutImportException("The manifest is not a treadmill-workout v4 bundle.");
      string toolVersion = root.GetProperty("tool_version").GetString() ?? string.Empty;
      if (!Version.TryParse(toolVersion, out Version? version) || version.Major != 4)
        throw new WorkoutImportException("Only treadmill-workout major version 4 bundles are supported.");
      string[] profiles = root.GetProperty("device_profile_ids").EnumerateArray()
        .Select(item => item.GetString() ?? string.Empty).ToArray();
      if (!profiles.Contains(OmegaProfile, StringComparer.Ordinal))
        throw new WorkoutImportException("The bundle does not include the Horizon Omega Z profile.");
      var artifacts = new Dictionary<string, string>(StringComparer.Ordinal);
      foreach (JsonProperty artifact in root.GetProperty("artifacts").EnumerateObject())
      {
        string digest = artifact.Value.GetString() ?? string.Empty;
        if (!IsSafePath(artifact.Name) || digest.Length != 64 ||
            !digest.All(static value => char.IsAsciiHexDigit(value)))
          throw new WorkoutImportException("The manifest artifact map is malformed.");
        artifacts.Add(artifact.Name, digest.ToLowerInvariant());
      }
      return new Manifest(toolVersion, artifacts);
    }
    catch (WorkoutImportException) { throw; }
    catch (Exception exception) when (exception is JsonException or KeyNotFoundException or InvalidOperationException or ArgumentException)
    {
      throw new WorkoutImportException("The bundle manifest is malformed.", exception);
    }
  }

  private static async Task VerifyArtifactsAsync(
    IReadOnlyDictionary<string, ZipArchiveEntry> entries,
    Manifest manifest,
    CancellationToken cancellationToken)
  {
    string[] actual = entries.Keys.Where(name => !string.Equals(name, ManifestName, StringComparison.Ordinal)).Order().ToArray();
    string[] expected = manifest.Artifacts.Keys.Order().ToArray();
    if (!actual.SequenceEqual(expected, StringComparer.Ordinal))
      throw new WorkoutImportException("The ZIP file set does not match its manifest.");
    foreach ((string path, string expectedDigest) in manifest.Artifacts)
    {
      await using Stream stream = entries[path].Open();
      byte[] digest = await SHA256.HashDataAsync(stream, cancellationToken);
      if (!string.Equals(Convert.ToHexStringLower(digest), expectedDigest, StringComparison.Ordinal))
        throw new WorkoutImportException($"Artifact digest mismatch: {path}.");
    }
  }

  private static async Task<IReadOnlyList<Dictionary<string, string>>> ReadIndexAsync(
    IReadOnlyDictionary<string, ZipArchiveEntry> entries,
    CancellationToken cancellationToken)
  {
    if (!entries.TryGetValue(IndexName, out ZipArchiveEntry? entry))
      throw new WorkoutImportException("The bundle is missing workout_index.csv.");
    string text = Encoding.UTF8.GetString(await ReadEntryAsync(entry, MaximumEntryBytes, cancellationToken));
    IReadOnlyList<IReadOnlyList<string>> records = ParseCsv(text);
    if (records.Count < 2) throw new WorkoutImportException("The workout index has no workout rows.");
    string[] headers = records[0].ToArray();
    if (headers.Distinct(StringComparer.Ordinal).Count() != headers.Length ||
        RequiredIndexColumns.Any(required => !headers.Contains(required, StringComparer.Ordinal)))
      throw new WorkoutImportException("The workout index is missing required or has duplicate columns.");
    var rows = new List<Dictionary<string, string>>(records.Count - 1);
    foreach (IReadOnlyList<string> record in records.Skip(1))
    {
      if (record.Count != headers.Length) throw new WorkoutImportException("A workout index row has the wrong number of columns.");
      rows.Add(headers.Select((header, index) => (header, value: record[index].Trim()))
        .ToDictionary(item => item.header, item => item.value, StringComparer.Ordinal));
    }
    if (rows.Count > 1_000) throw new WorkoutImportException("The workout index exceeds 1,000 variant rows.");
    return rows;
  }

  private static IReadOnlyList<IReadOnlyList<string>> ParseCsv(string text)
  {
    var records = new List<IReadOnlyList<string>>();
    var row = new List<string>();
    var field = new StringBuilder();
    bool quoted = false;
    for (int index = 0; index < text.Length; index++)
    {
      char current = text[index];
      if (quoted)
      {
        if (current == '"' && index + 1 < text.Length && text[index + 1] == '"') { field.Append('"'); index++; }
        else if (current == '"') quoted = false;
        else field.Append(current);
      }
      else if (current == '"' && field.Length == 0) quoted = true;
      else if (current == ',') { row.Add(field.ToString()); field.Clear(); }
      else if (current == '\r') { }
      else if (current == '\n')
      {
        row.Add(field.ToString()); field.Clear();
        if (row.Any(static value => value.Length > 0)) records.Add(row.ToArray());
        row = [];
      }
      else field.Append(current);
    }
    if (quoted) throw new WorkoutImportException("The workout index contains an unterminated quoted field.");
    if (field.Length > 0 || row.Count > 0)
    {
      row.Add(field.ToString());
      if (row.Any(static value => value.Length > 0)) records.Add(row.ToArray());
    }
    return records;
  }

  private static void ValidateRow(IReadOnlyDictionary<string, string> row, string slot)
  {
    ValidateText("canonical_slot", row["canonical_slot"], MaximumCanonicalSlotLength, allowEmpty: false);
    ValidateText("session_id", row["session_id"], MaximumSessionIdLength, allowEmpty: false);
    ValidateText("intended_control_mode", row["intended_control_mode"], MaximumControlModeLength, allowEmpty: false);
    ValidateText("selection_rule", row["selection_rule"], MaximumSelectionRuleLength, allowEmpty: false);
    ValidateText("alternative_of", row["alternative_of"], MaximumCanonicalSlotLength, allowEmpty: true);
    if (!string.Equals(row["perform_exactly_one_variant"], "true", StringComparison.OrdinalIgnoreCase))
      throw new WorkoutImportException($"Slot {slot} does not require exactly one selected variant.");
    string variant = row["variant"];
    if (variant is not ("primary" or "hr-alternative" or "fixed-fallback" or "omega-recovery-incline"))
      throw new WorkoutImportException($"Slot {slot} uses unsupported variant '{variant}'.");
    string alternativeOf = row["alternative_of"];
    if (variant == "primary" ? alternativeOf.Length != 0 : !string.Equals(alternativeOf, slot, StringComparison.Ordinal))
      throw new WorkoutImportException($"Slot {slot} has an invalid alternative relationship.");
    string path = row["horizon_omega_z_file"];
    if (!path.StartsWith(OmegaPrefix, StringComparison.Ordinal) || !path.EndsWith(".xml", StringComparison.OrdinalIgnoreCase) || !IsSafePath(path))
      throw new WorkoutImportException($"Slot {slot} does not reference a safe Omega workout file.");
    if (string.IsNullOrWhiteSpace(row["session_id"]) || string.IsNullOrWhiteSpace(row["title"]) ||
        string.IsNullOrWhiteSpace(row["selection_rule"]))
      throw new WorkoutImportException($"Slot {slot} has incomplete index metadata.");
  }

  private static void ValidateText(string field, string value, int maximumLength, bool allowEmpty)
  {
    if ((!allowEmpty && string.IsNullOrWhiteSpace(value)) || value.Length > maximumLength ||
        value.Any(static character => char.IsControl(character)))
      throw new WorkoutImportException($"Workout index field {field} is empty, too long, or contains control characters.");
  }

  private static async Task<(string PlanName, string Category)> ReadPlanIdentityAsync(
    IReadOnlyDictionary<string, ZipArchiveEntry> entries,
    string fileName,
    CancellationToken cancellationToken)
  {
    string fallback = NormalizeTitle(Path.GetFileNameWithoutExtension(fileName));
    if (!entries.TryGetValue("config.json", out ZipArchiveEntry? entry)) return (fallback, "Generated");
    try
    {
      using JsonDocument document = JsonDocument.Parse(await ReadEntryAsync(entry, MaximumEntryBytes, cancellationToken));
      string name = document.RootElement.TryGetProperty("plan_name", out JsonElement planName) ? planName.GetString() ?? fallback : fallback;
      string category = document.RootElement.TryGetProperty("preset_id", out JsonElement preset) ? preset.GetString() ?? "Generated" : "Generated";
      return (NormalizeTitle(name), NormalizeCategory(category));
    }
    catch (Exception exception) when (exception is JsonException or InvalidOperationException)
    {
      throw new WorkoutImportException("The bundled config.json is malformed.", exception);
    }
  }

  private static async Task<byte[]> ReadEntryAsync(
    ZipArchiveEntry entry,
    long maximumBytes,
    CancellationToken cancellationToken)
  {
    if (entry.Length > maximumBytes) throw new WorkoutImportException($"ZIP entry {entry.FullName} is too large.");
    await using Stream stream = entry.Open();
    using var buffer = new MemoryStream((int)entry.Length);
    await stream.CopyToAsync(buffer, cancellationToken);
    if (buffer.Length > maximumBytes) throw new WorkoutImportException($"ZIP entry {entry.FullName} is too large.");
    return buffer.ToArray();
  }

  private static int ParsePositive(string value, string field)
  {
    if (!int.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out int parsed) || parsed < 1)
      throw new WorkoutImportException($"Workout index field {field} must be a positive integer.");
    return parsed;
  }

  private static string NormalizeTitle(string value)
  {
    string normalized = string.Join(' ', value.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries)).Trim();
    if (normalized.Length == 0) normalized = "Generated workout set";
    return normalized.Length <= 140 ? normalized : normalized[..140];
  }

  private static string NormalizeCategory(string value)
  {
    string normalized = NormalizeTitle(value).Replace('_', ' ');
    return normalized.Length <= 40 ? normalized : normalized[..40];
  }

  private sealed record Manifest(string ToolVersion, IReadOnlyDictionary<string, string> Artifacts);
}
