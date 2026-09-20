using Dynastream.Fit;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Protocols.Exports;

public static class GarminFitActivityMerger
{
  private const byte MinimumAcceptedHeartRate = 30;
  private const byte MaximumAcceptedHeartRate = 250;
  private const byte MaximumValidFitHeartRate = byte.MaxValue - 1;

  private static readonly ushort[] RetainedStaticMessageTypes =
  [
    MesgNum.FileCreator,
    MesgNum.DeviceSettings,
    MesgNum.UserProfile,
    MesgNum.TrainingSettings,
    MesgNum.Sport,
  ];

  private static readonly byte[] WatchOnlyRecordFields =
  [
    RecordMesg.FieldDefNum.Cadence,
    RecordMesg.FieldDefNum.Power,
    RecordMesg.FieldDefNum.CycleLength,
    RecordMesg.FieldDefNum.Temperature,
    RecordMesg.FieldDefNum.Cycles,
    RecordMesg.FieldDefNum.TotalCycles,
    RecordMesg.FieldDefNum.LeftRightBalance,
    RecordMesg.FieldDefNum.VerticalOscillation,
    RecordMesg.FieldDefNum.StanceTimePercent,
    RecordMesg.FieldDefNum.StanceTime,
    RecordMesg.FieldDefNum.Cadence256,
    RecordMesg.FieldDefNum.FractionalCadence,
    RecordMesg.FieldDefNum.LeftPco,
    RecordMesg.FieldDefNum.RightPco,
    RecordMesg.FieldDefNum.LeftPowerPhase,
    RecordMesg.FieldDefNum.LeftPowerPhasePeak,
    RecordMesg.FieldDefNum.RightPowerPhase,
    RecordMesg.FieldDefNum.RightPowerPhasePeak,
    RecordMesg.FieldDefNum.VerticalRatio,
    RecordMesg.FieldDefNum.StanceTimeBalance,
    RecordMesg.FieldDefNum.StepLength,
    RecordMesg.FieldDefNum.RespirationRate,
    RecordMesg.FieldDefNum.EnhancedRespirationRate,
    RecordMesg.FieldDefNum.CoreTemperature,
  ];

  public static byte[] Merge(byte[] watchFit, StoredWorkoutSession localSession)
  {
    ArgumentNullException.ThrowIfNull(watchFit);
    ArgumentNullException.ThrowIfNull(localSession);
    if (watchFit.Length is 0 or > 16 * 1024 * 1024)
      throw new ArgumentOutOfRangeException(nameof(watchFit), "The watch FIT must be non-empty and at most 16 MiB.");
    if (localSession.StartedAt is null || localSession.EndedAt is null || localSession.Samples.Count == 0)
      throw new InvalidOperationException("A completed local session with samples is required for FIT merge.");

    List<Mesg> watchMessages = DecodeAndClone(watchFit);
    if (!watchMessages.Any(static message =>
          message.Num == MesgNum.Record && new RecordMesg(message).GetTimestamp() is not null))
      throw new InvalidDataException("The watch FIT contains no timestamped record messages.");

    IReadOnlyList<SessionSample> samples = SessionSampleTimeline.Normalize(localSession.Samples);
    StoredWorkoutSession orderedSession = localSession with { Samples = samples };
    List<Mesg> localMessages = DecodeAndClone(SessionFitActivityExporter.Export(orderedSession));
    DateTimeOffset effectiveEnd = SessionFitActivityExporter.GetEffectiveEnd(orderedSession);
    uint? expectedLocalTimestamp = ShiftedLocalTimestamp(watchMessages, effectiveEnd);
    List<Mesg> canonical = BuildCanonicalMessages(
      watchMessages,
      localMessages,
      orderedSession,
      out IReadOnlyList<byte?> expectedHeartRates);

    using var output = new MemoryStream();
    var encoder = new Encode(ProtocolVersion.V20);
    encoder.Open(output);
    foreach (Mesg message in canonical) encoder.Write(message);
    encoder.Close();

    byte[] merged = output.ToArray();
    using (var validation = new MemoryStream(merged, writable: false))
    {
      var decoder = new Decode();
      if (!decoder.IsFIT(validation)) throw new InvalidDataException("The merged FIT header is invalid.");
      validation.Position = 0;
      if (!decoder.CheckIntegrity(validation)) throw new InvalidDataException("The merged FIT CRC is invalid.");
    }

    EnsureCanonicalCoherence(
      DecodeAndClone(merged),
      orderedSession,
      expectedLocalTimestamp,
      expectedHeartRates);
    return merged;
  }

  private static List<Mesg> BuildCanonicalMessages(
    IReadOnlyList<Mesg> watchMessages,
    IReadOnlyList<Mesg> localMessages,
    StoredWorkoutSession localSession,
    out IReadOnlyList<byte?> expectedHeartRates)
  {
    Dynastream.Fit.DateTime start = new(localSession.StartedAt!.Value.UtcDateTime);
    var result = new List<Mesg>(localMessages.Count + RetainedStaticMessageTypes.Length + 8)
    {
      ReplacementFileId(watchMessages, localSession, start),
    };

    foreach (ushort messageType in RetainedStaticMessageTypes)
    {
      Mesg? metadata = watchMessages.FirstOrDefault(message => message.Num == messageType);
      if (metadata is null) continue;
      Mesg retained = CloneStandardFieldsOnly(metadata);
      if (messageType == MesgNum.UserProfile)
      {
        var profile = new UserProfileMesg(retained);
        if (!IsAcceptedHeartRate(profile.GetDefaultMaxHeartRate()))
          RemoveField(profile, UserProfileMesg.FieldDefNum.DefaultMaxHeartRate);
        if (!IsAcceptedHeartRate(profile.GetRestingHeartRate()))
          RemoveField(profile, UserProfileMesg.FieldDefNum.RestingHeartRate);
        retained = profile;
      }
      result.Add(retained);
    }

    AddValidatedHeartRateZoneMetadata(result, watchMessages);

    // DeviceInfo carries useful Garmin/watch/sensor provenance, but devices commonly
    // write the same identity at both ends of an activity. Retain one identity per
    // device index and anchor it to the rebuilt local timeline.
    var retainedDeviceIndexes = new HashSet<byte>();
    foreach (Mesg message in watchMessages.Where(static message => message.Num == MesgNum.DeviceInfo))
    {
      var device = new DeviceInfoMesg(CloneStandardFieldsOnly(message));
      byte index = device.GetDeviceIndex() ?? byte.MaxValue;
      if (!retainedDeviceIndexes.Add(index)) continue;
      device.SetTimestamp(start);
      result.Add(device);
    }

    RecordMesg[] watchRecords = watchMessages
      .Where(static message => message.Num == MesgNum.Record)
      .Select(static message => new RecordMesg(message))
      .Where(static record => record.GetTimestamp() is not null)
      .OrderBy(static record => record.GetTimestamp()!.GetTimeStamp())
      .ToArray();
    float altitudeBaseline = watchRecords
      .Select(static record => record.GetEnhancedAltitude() ?? record.GetAltitude())
      .FirstOrDefault(static altitude => altitude is not null) ?? 0;
    IReadOnlyDictionary<Mesg, RecordMesg> matchedWatchRecords = MatchWatchRecords(localMessages, watchRecords);
    SessionMesg? watchSession = watchMessages
      .Where(static message => message.Num == MesgNum.Session)
      .Select(static message => new SessionMesg(message))
      .FirstOrDefault();
    var canonicalHeartRates = new List<byte?>(localSession.Samples.Count);
    CanonicalHeartRateStatistics? canonicalHeartRateStatistics = null;
    foreach (Mesg message in localMessages)
    {
      switch (message.Num)
      {
        case MesgNum.Event:
          result.Add(new EventMesg(message));
          break;
        case MesgNum.Record:
          {
            var record = new RecordMesg(message);
            if (matchedWatchRecords.TryGetValue(message, out RecordMesg? watchRecord))
            {
              CopyFields(record, watchRecord, WatchOnlyRecordFields);
              if (!IsValidFitHeartRate(record.GetHeartRate()) &&
                  watchRecord.GetHeartRate() is { } watchHeartRate &&
                  IsPlausibleHeartRate(watchHeartRate))
                record.SetHeartRate(watchHeartRate);
            }
            if (record.GetEnhancedAltitude() is { } enhancedAltitude)
              record.SetEnhancedAltitude(enhancedAltitude + altitudeBaseline);
            if (record.GetAltitude() is { } altitude)
              record.SetAltitude(altitude + altitudeBaseline);
            RemoveField(record, RecordMesg.FieldDefNum.Zone);
            canonicalHeartRates.Add(record.GetHeartRate());
            result.Add(record);
            break;
          }
        case MesgNum.Lap:
          {
            var lap = new LapMesg(message);
            canonicalHeartRateStatistics ??= CalculateCanonicalHeartRateStatistics(
              localSession.Samples,
              canonicalHeartRates);
            SetHeartRateSummary(lap, canonicalHeartRateStatistics);
            RemoveField(lap, LapMesg.FieldDefNum.TimeInHrZone);
            result.Add(lap);
            break;
          }
        case MesgNum.Session:
          {
            var session = new SessionMesg(message);
            if (watchSession is not null)
              CopyFields(session, watchSession, [SessionMesg.FieldDefNum.SportProfileName]);
            canonicalHeartRateStatistics ??= CalculateCanonicalHeartRateStatistics(
              localSession.Samples,
              canonicalHeartRates);
            SetHeartRateSummary(session, canonicalHeartRateStatistics);
            RemoveField(session, SessionMesg.FieldDefNum.TimeInHrZone);
            result.Add(session);
            break;
          }
        case MesgNum.Activity:
          {
            var activity = new ActivityMesg(message);
            if (ShiftedLocalTimestamp(
                  watchMessages,
                  SessionFitActivityExporter.GetEffectiveEnd(localSession)) is { } localTimestamp)
              activity.SetLocalTimestamp(localTimestamp);
            result.Add(activity);
            break;
          }
      }
    }

    // Retain only a validated Garmin/user zone definition and raw local heart
    // rate. Derived summaries and unverifiable watch aggregates are deliberately
    // omitted so Garmin receives one internally consistent activity timeline.
    expectedHeartRates = canonicalHeartRates;
    return result;
  }

  private static CanonicalHeartRateStatistics CalculateCanonicalHeartRateStatistics(
    IReadOnlyList<SessionSample> samples,
    IReadOnlyList<byte?> heartRates)
  {
    if (samples.Count != heartRates.Count)
      throw new InvalidDataException("The canonical heart-rate timeline does not match the local samples.");
    byte[] values = heartRates
      .Where(IsValidFitHeartRate)
      .Select(static value => value!.Value)
      .ToArray();
    if (values.Length == 0) return new(null, null, null);

    double weightedTotal = 0;
    long totalTicks = 0;
    for (var index = 1; index < samples.Count; index++)
    {
      if (!IsValidFitHeartRate(heartRates[index])) continue;
      long ticks = (samples[index].Elapsed - samples[index - 1].Elapsed).Ticks;
      if (ticks == 0) continue;
      weightedTotal += heartRates[index]!.Value * ticks;
      totalTicks = checked(totalTicks + ticks);
    }
    double average = totalTicks == 0
      ? values.Average(static value => (double)value)
      : weightedTotal / totalTicks;
    return new(average, values.Min(), values.Max());
  }

  private static void SetHeartRateSummary(LapMesg message, CanonicalHeartRateStatistics statistics)
  {
    RemoveField(message, LapMesg.FieldDefNum.AvgHeartRate);
    RemoveField(message, LapMesg.FieldDefNum.MinHeartRate);
    RemoveField(message, LapMesg.FieldDefNum.MaxHeartRate);
    if (statistics.Average is { } average) message.SetAvgHeartRate(ToFitHeartRate(average));
    if (statistics.Minimum is { } minimum) message.SetMinHeartRate(minimum);
    if (statistics.Maximum is { } maximum) message.SetMaxHeartRate(maximum);
  }

  private static void SetHeartRateSummary(SessionMesg message, CanonicalHeartRateStatistics statistics)
  {
    RemoveField(message, SessionMesg.FieldDefNum.AvgHeartRate);
    RemoveField(message, SessionMesg.FieldDefNum.MinHeartRate);
    RemoveField(message, SessionMesg.FieldDefNum.MaxHeartRate);
    if (statistics.Average is { } average) message.SetAvgHeartRate(ToFitHeartRate(average));
    if (statistics.Minimum is { } minimum) message.SetMinHeartRate(minimum);
    if (statistics.Maximum is { } maximum) message.SetMaxHeartRate(maximum);
  }

  private static byte ToFitHeartRate(double heartRate) =>
    (byte)Math.Clamp(
      Math.Round(heartRate, MidpointRounding.AwayFromZero),
      byte.MinValue,
      MaximumValidFitHeartRate);

  private sealed record CanonicalHeartRateStatistics(
    double? Average,
    byte? Minimum,
    byte? Maximum);

  private static FileIdMesg ReplacementFileId(
    IReadOnlyList<Mesg> watchMessages,
    StoredWorkoutSession localSession,
    Dynastream.Fit.DateTime start)
  {
    var fileId = new FileIdMesg(CloneStandardFieldsOnly(
      watchMessages.Single(static message => message.Num == MesgNum.FileId)));
    uint watchSerial = fileId.GetSerialNumber() ?? 0u;
    uint replacementSerial = watchSerial ^ BitConverter.ToUInt32(localSession.Definition.SessionId.ToByteArray(), 0) ^ 0x4D455247u;
    if (replacementSerial == watchSerial) replacementSerial ^= 1u;
    fileId.SetSerialNumber(replacementSerial);
    fileId.SetTimeCreated(start);
    return fileId;
  }

  private static void RemoveField(Mesg message, byte fieldNumber)
  {
    Field? field = message.GetField(fieldNumber);
    if (field is not null) message.RemoveField(field);
  }

  private static void AddValidatedHeartRateZoneMetadata(
    ICollection<Mesg> result,
    IReadOnlyList<Mesg> watchMessages)
  {
    Mesg[] targets = watchMessages.Where(static message => message.Num == MesgNum.ZonesTarget).ToArray();
    HrZoneMesg[] zones = watchMessages
      .Where(static message => message.Num == MesgNum.HrZone)
      .Select(static message => new HrZoneMesg(message))
      .ToArray();
    if (targets.Length != 1 || zones.Length == 0) return;

    var target = new ZonesTargetMesg(targets[0]);
    if (!IsAcceptedHeartRate(target.GetMaxHeartRate()) ||
        !IsAcceptedHeartRate(target.GetThresholdHeartRate()))
      return;

    var validated = new List<(ushort Index, byte HighBpm, HrZoneMesg Zone)>(zones.Length);
    foreach (HrZoneMesg zone in zones)
    {
      if (zone.GetMessageIndex() is not { } index ||
          zone.GetHighBpm() is not { } highBpm ||
          !IsAcceptedHeartRate(highBpm))
        return;
      validated.Add((index, highBpm, zone));
    }

    validated.Sort(static (left, right) => left.Index.CompareTo(right.Index));
    for (var position = 0; position < validated.Count; position++)
    {
      if (validated[position].Index != position) return;
      if (position > 0 && validated[position].HighBpm <= validated[position - 1].HighBpm) return;
    }

    result.Add(CloneStandardFieldsOnly(target));
    foreach (var entry in validated) result.Add(CloneStandardFieldsOnly(entry.Zone));
  }

  private static bool IsAcceptedHeartRate(byte? value) =>
    value is null or (>= MinimumAcceptedHeartRate and <= MaximumAcceptedHeartRate);

  private static bool IsPlausibleHeartRate(byte? value) =>
    value is >= MinimumAcceptedHeartRate and <= MaximumAcceptedHeartRate;

  private static bool IsValidFitHeartRate(byte? value) =>
    value is not null && value != byte.MaxValue;

  private static byte? NormalizeMissingFitHeartRate(byte? value) =>
    IsValidFitHeartRate(value) ? value : null;

  private static IReadOnlyDictionary<Mesg, RecordMesg> MatchWatchRecords(
    IReadOnlyList<Mesg> localMessages,
    IReadOnlyList<RecordMesg> watchRecords)
  {
    var localRecords = localMessages
      .Where(static message => message.Num == MesgNum.Record)
      .Select(static message => (Message: message, Timestamp: new RecordMesg(message).GetTimestamp()?.GetTimeStamp()))
      .Where(static record => record.Timestamp is not null)
      .Select(static record => (record.Message, Timestamp: record.Timestamp!.Value))
      .OrderBy(static record => record.Timestamp)
      .ToArray();
    if (localRecords.Length == 0 || watchRecords.Count == 0)
      return new Dictionary<Mesg, RecordMesg>(ReferenceEqualityComparer.Instance);

    uint[] localTimestamps = localRecords.Select(static record => record.Timestamp).ToArray();
    var localAssigned = new bool[localRecords.Length];
    int[] watchToLocal = Enumerable.Repeat(-1, watchRecords.Count).ToArray();
    var matches = new Dictionary<Mesg, RecordMesg>(ReferenceEqualityComparer.Instance);

    // Reserve exact timestamp matches first. A nearby earlier watch sample must
    // never steal the local slot from a later exact sample.
    var exactLocalIndexes = new Dictionary<uint, Queue<int>>();
    for (var localIndex = 0; localIndex < localTimestamps.Length; localIndex++)
    {
      if (!exactLocalIndexes.TryGetValue(localTimestamps[localIndex], out Queue<int>? indexes))
      {
        indexes = new Queue<int>();
        exactLocalIndexes.Add(localTimestamps[localIndex], indexes);
      }
      indexes.Enqueue(localIndex);
    }
    for (var watchIndex = 0; watchIndex < watchRecords.Count; watchIndex++)
    {
      uint watchTimestamp = watchRecords[watchIndex].GetTimestamp()!.GetTimeStamp();
      if (!exactLocalIndexes.TryGetValue(watchTimestamp, out Queue<int>? indexes) || indexes.Count == 0) continue;
      int localIndex = indexes.Dequeue();
      watchToLocal[watchIndex] = localIndex;
      localAssigned[localIndex] = true;
    }

    int[] nextExactLocal = Enumerable.Repeat(-1, watchRecords.Count).ToArray();
    var nextExact = -1;
    for (var watchIndex = watchRecords.Count - 1; watchIndex >= 0; watchIndex--)
    {
      nextExactLocal[watchIndex] = nextExact;
      if (watchToLocal[watchIndex] >= 0) nextExact = watchToLocal[watchIndex];
    }

    var previousLocal = -1;
    for (var watchIndex = 0; watchIndex < watchRecords.Count; watchIndex++)
    {
      if (watchToLocal[watchIndex] >= 0)
      {
        previousLocal = watchToLocal[watchIndex];
        continue;
      }

      uint watchTimestamp = watchRecords[watchIndex].GetTimestamp()!.GetTimeStamp();
      uint earliest = watchTimestamp > 5 ? watchTimestamp - 5 : 0;
      ulong latest = (ulong)watchTimestamp + 5;
      int lowerBound = Math.Max(previousLocal + 1, LowerBound(localTimestamps, earliest));
      int upperBound = nextExactLocal[watchIndex] >= 0
        ? nextExactLocal[watchIndex] - 1
        : localRecords.Length - 1;
      int nearestIndex = -1;
      long nearestDistance = long.MaxValue;
      for (var localIndex = lowerBound;
           localIndex <= upperBound && localIndex < localTimestamps.Length && localTimestamps[localIndex] <= latest;
           localIndex++)
      {
        if (localAssigned[localIndex]) continue;
        long distance = TimestampDistance(localTimestamps[localIndex], watchTimestamp);
        if (distance < nearestDistance)
        {
          nearestIndex = localIndex;
          nearestDistance = distance;
        }
      }
      if (nearestIndex < 0) continue;
      watchToLocal[watchIndex] = nearestIndex;
      localAssigned[nearestIndex] = true;
      previousLocal = nearestIndex;
    }

    for (var watchIndex = 0; watchIndex < watchToLocal.Length; watchIndex++)
    {
      int localIndex = watchToLocal[watchIndex];
      if (localIndex < 0) continue;
      matches.Add(localRecords[localIndex].Message, watchRecords[watchIndex]);
    }
    return matches;
  }

  private static int LowerBound(IReadOnlyList<uint> sortedTimestamps, uint timestamp)
  {
    int low = 0;
    int high = sortedTimestamps.Count;
    while (low < high)
    {
      int middle = low + ((high - low) / 2);
      if (sortedTimestamps[middle] < timestamp) low = middle + 1;
      else high = middle;
    }
    return low;
  }

  private static void CopyFields(Mesg target, Mesg source, IEnumerable<byte> fieldNumbers)
  {
    foreach (byte fieldNumber in fieldNumbers)
    {
      Field? field = source.GetField(fieldNumber);
      if (field is not null) target.SetField(new Field(field));
    }
  }

  private static uint? ShiftedLocalTimestamp(IReadOnlyList<Mesg> watchMessages, DateTimeOffset localEnd)
  {
    ActivityMesg? watchActivity = watchMessages
      .Where(static message => message.Num == MesgNum.Activity)
      .Select(static message => new ActivityMesg(message))
      .FirstOrDefault();
    if (watchActivity?.GetTimestamp() is not { } watchTimestamp ||
        watchActivity.GetLocalTimestamp() is not { } watchLocalTimestamp)
      return null;

    long offsetSeconds = (long)watchLocalTimestamp - watchTimestamp.GetTimeStamp();
    long shifted = (long)new Dynastream.Fit.DateTime(localEnd.UtcDateTime).GetTimeStamp() + offsetSeconds;
    return shifted is >= uint.MinValue and <= uint.MaxValue ? (uint)shifted : null;
  }

  private static Mesg CloneStandardFieldsOnly(Mesg source)
  {
    var clone = new Mesg(source.Name, source.Num);
    foreach (Field field in source.Fields) clone.SetField(new Field(field));
    return clone;
  }

  private static long TimestampDistance(uint left, uint right) => Math.Abs((long)left - right);


  private static List<Mesg> DecodeAndClone(byte[] source)
  {
    using var input = new MemoryStream(source, writable: false);
    var decoder = new Decode();
    if (!decoder.IsFIT(input)) throw new InvalidDataException("The watch activity is not a FIT file.");
    input.Position = 0;
    if (!decoder.CheckIntegrity(input)) throw new InvalidDataException("The watch FIT CRC is invalid.");
    input.Position = 0;
    var messages = new List<Mesg>();
    decoder.MesgEvent += (_, args) => messages.Add(new Mesg(args.mesg));
    if (!decoder.Read(input) || messages.Count == 0) throw new InvalidDataException("The watch FIT could not be decoded.");
    Mesg[] fileIds = messages.Where(static message => message.Num == MesgNum.FileId).ToArray();
    if (fileIds.Length != 1 || new FileIdMesg(fileIds[0]).GetType() != Dynastream.Fit.File.Activity)
      throw new InvalidDataException("The watch FIT must contain exactly one Activity File Id message.");
    return messages;
  }

  private static void EnsureCanonicalCoherence(
    IReadOnlyList<Mesg> messages,
    StoredWorkoutSession session,
    uint? expectedLocalTimestamp,
    IReadOnlyList<byte?> expectedHeartRates)
  {
    ushort[] allowedTypes =
    [
      MesgNum.FileId,
      MesgNum.FileCreator,
      MesgNum.DeviceSettings,
      MesgNum.UserProfile,
      MesgNum.ZonesTarget,
      MesgNum.HrZone,
      MesgNum.TrainingSettings,
      MesgNum.Sport,
      MesgNum.DeviceInfo,
      MesgNum.Event,
      MesgNum.Record,
      MesgNum.Lap,
      MesgNum.Session,
      MesgNum.Activity,
    ];
    if (messages.Any(message => !allowedTypes.Contains(message.Num)))
      throw new InvalidDataException("The merged FIT contains a non-canonical watch timeline message.");

    FileIdMesg fileId = Single(messages, MesgNum.FileId, static message => new FileIdMesg(message), "File Id");
    LapMesg lap = Single(messages, MesgNum.Lap, static message => new LapMesg(message), "Lap");
    SessionMesg sessionMessage = Single(messages, MesgNum.Session, static message => new SessionMesg(message), "Session");
    ActivityMesg activity = Single(messages, MesgNum.Activity, static message => new ActivityMesg(message), "Activity");
    RecordMesg[] records = messages.Where(static message => message.Num == MesgNum.Record).Select(static message => new RecordMesg(message)).ToArray();
    EventMesg[] events = messages.Where(static message => message.Num == MesgNum.Event).Select(static message => new EventMesg(message)).ToArray();
    DeviceInfoMesg[] devices = messages.Where(static message => message.Num == MesgNum.DeviceInfo).Select(static message => new DeviceInfoMesg(message)).ToArray();

    uint start = new Dynastream.Fit.DateTime(session.StartedAt!.Value.UtcDateTime).GetTimeStamp();
    DateTimeOffset effectiveEnd = SessionFitActivityExporter.GetEffectiveEnd(session);
    uint end = new Dynastream.Fit.DateTime(effectiveEnd.UtcDateTime).GetTimeStamp();
    Require(fileId.GetTimeCreated()?.GetTimeStamp() == start, "The merged FIT File Id is not anchored to the local start.");
    Require(records.Length == session.Samples.Count, "The merged FIT record count does not match the local session.");
    Require(expectedHeartRates.Count == records.Length, "The merged FIT heart-rate expectation count is invalid.");
    for (var index = 0; index < records.Length; index++)
    {
      uint expected = new Dynastream.Fit.DateTime(session.Samples[index].CapturedAt.UtcDateTime).GetTimeStamp();
      Require(records[index].GetTimestamp()?.GetTimeStamp() == expected, "The merged FIT record timeline does not match the local session.");
      Require(
        NormalizeMissingFitHeartRate(records[index].GetHeartRate()) ==
        NormalizeMissingFitHeartRate(expectedHeartRates[index]),
        "The merged FIT heart-rate timeline does not match the canonical source selection.");
      Require(records[index].GetZone() is null, "The merged FIT contains a derived record heart-rate zone.");
      Require(!records[index].DeveloperFields.Any(), "The merged FIT contains a watch developer field on a local record.");
    }

    SessionEvent[] timerHistory = SessionFitActivityExporter.NormalizeTimerHistory(session).ToArray();
    Require(events.Length == timerHistory.Length + 2, "The merged FIT timer history does not match persisted pause/resume history.");
    Require(events[0].GetEvent() == Event.Timer && events[0].GetEventType() == EventType.Start && events[0].GetTimestamp()?.GetTimeStamp() == start,
      "The merged FIT timer start does not match the local session.");
    for (var index = 0; index < timerHistory.Length; index++)
    {
      EventMesg timerEvent = events[index + 1];
      SessionEvent historyEvent = timerHistory[index];
      EventType expectedType = historyEvent is SessionPausedEvent ? EventType.Stop : EventType.Start;
      Require(timerEvent.GetEvent() == Event.Timer && timerEvent.GetEventType() == expectedType &&
              timerEvent.GetTimestamp()?.GetTimeStamp() ==
                new Dynastream.Fit.DateTime(historyEvent.OccurredAt.UtcDateTime).GetTimeStamp(),
        "The merged FIT timer pause/resume history does not match the local session.");
    }
    EventMesg finalTimerEvent = events[^1];
    Require(finalTimerEvent.GetEvent() == Event.Timer && finalTimerEvent.GetEventType() == EventType.StopAll &&
            finalTimerEvent.GetTimestamp()?.GetTimeStamp() == end,
      "The merged FIT timer stop does not match the local session.");

    Require(lap.GetMessageIndex() == 0 && sessionMessage.GetFirstLapIndex() == 0 && sessionMessage.GetNumLaps() == 1,
      "The merged FIT does not contain exactly one indexed canonical lap.");
    Require(lap.GetStartTime()?.GetTimeStamp() == start && lap.GetTimestamp()?.GetTimeStamp() == end,
      "The merged FIT lap timeline does not match the local session.");
    Require(sessionMessage.GetStartTime()?.GetTimeStamp() == start && sessionMessage.GetTimestamp()?.GetTimeStamp() == end,
      "The merged FIT session timeline does not match the local session.");
    Require(activity.GetTimestamp()?.GetTimeStamp() == end && activity.GetNumSessions() == 1,
      "The merged FIT activity does not match the local session.");
    Require(activity.GetLocalTimestamp() == expectedLocalTimestamp,
      "The merged FIT activity local timestamp does not preserve the watch time-zone offset.");
    float expectedElapsedSeconds = (float)Math.Max(
      session.Duration.TotalSeconds,
      (effectiveEnd - session.StartedAt.Value).TotalSeconds);
    float expectedTimerSeconds = (float)session.Duration.TotalSeconds;
    Require(NearlyEqual(lap.GetTotalTimerTime(), sessionMessage.GetTotalTimerTime()) &&
            NearlyEqual(lap.GetTotalElapsedTime(), sessionMessage.GetTotalElapsedTime()) &&
            NearlyEqual(lap.GetTotalElapsedTime(), expectedElapsedSeconds) &&
            NearlyEqual(lap.GetTotalTimerTime(), expectedTimerSeconds) &&
            NearlyEqual(lap.GetTotalDistance(), sessionMessage.GetTotalDistance()) &&
            lap.GetTotalCalories() == sessionMessage.GetTotalCalories() &&
            lap.GetAvgHeartRate() == sessionMessage.GetAvgHeartRate() &&
            lap.GetMinHeartRate() == sessionMessage.GetMinHeartRate() &&
            lap.GetMaxHeartRate() == sessionMessage.GetMaxHeartRate() &&
            NearlyEqual(activity.GetTotalTimerTime(), sessionMessage.GetTotalTimerTime()),
      "The merged FIT lap, session, and activity summaries disagree.");
    Require(lap.GetNumTimeInHrZone() == 0 && sessionMessage.GetNumTimeInHrZone() == 0,
      "The merged FIT contains conflicting derived heart-rate-zone summaries.");
    Require(sessionMessage.GetTotalTrainingEffect() is null &&
            sessionMessage.GetTotalAnaerobicTrainingEffect() is null &&
            sessionMessage.GetTrainingStressScore() is null &&
            sessionMessage.GetIntensityFactor() is null &&
            sessionMessage.GetTrainingLoadPeak() is null,
      "The merged FIT contains a watch-derived training summary for the replaced record timeline.");
    Require(devices.All(device => device.GetTimestamp()?.GetTimeStamp() == start),
      "The merged FIT contains device provenance outside the local timeline.");
  }

  private static T Single<T>(
    IReadOnlyList<Mesg> messages,
    ushort messageType,
    Func<Mesg, T> convert,
    string label)
  {
    Mesg[] matches = messages.Where(message => message.Num == messageType).ToArray();
    if (matches.Length != 1) throw new InvalidDataException($"The merged FIT must contain exactly one {label} message.");
    return convert(matches[0]);
  }

  private static bool NearlyEqual(float? left, float? right) =>
    left is { } leftValue && right is { } rightValue && Math.Abs(leftValue - rightValue) <= 0.01f;

  private static void Require(bool condition, string message)
  {
    if (!condition) throw new InvalidDataException(message);
  }
}
