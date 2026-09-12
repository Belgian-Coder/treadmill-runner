namespace TreadmillRunner.PolarH10Simulator;

internal static class Program
{
  public static async Task<int> Main(string[] args)
  {
    if (args.Any(static argument => argument is "--help" or "-h"))
    {
      PrintUsage();
      return 0;
    }

    try
    {
      PolarH10SimulatorOptions options = ParseOptions(args);
#if WINDOWS
      var stateMachine = new PolarH10SimulatorStateMachine(options);
      await using var peripheral = new WindowsPolarH10Peripheral(stateMachine);
      using var cancellation = new CancellationTokenSource();
      Console.CancelKeyPress += (_, eventArgs) =>
      {
        eventArgs.Cancel = true;
        cancellation.Cancel();
      };
      await peripheral.StartAsync(cancellation.Token).ConfigureAwait(false);
      Console.WriteLine($"{options.DeviceName} is advertising Heart Rate 0x180D and Polar PFTP 0xFEEE.");
      Console.WriteLine("Press Ctrl+C to stop. BLE discovery/connection from another machine is required for an end-to-end test.");
      try { await Task.Delay(Timeout.InfiniteTimeSpan, cancellation.Token).ConfigureAwait(false); }
      catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
      return 0;
#else
      Console.Error.WriteLine("This executable was built for the portable target. Run with --framework net10.0-windows10.0.22621.0 on Windows.");
      return 3;
#endif
    }
    catch (Exception exception) when (exception is ArgumentException or FormatException or OverflowException)
    {
      Console.Error.WriteLine($"Configuration error: {exception.Message}");
      PrintUsage();
      return 2;
    }
    catch (Exception exception)
    {
      Console.Error.WriteLine($"Could not start the simulator: {exception.Message}");
      return 1;
    }
  }

  private static PolarH10SimulatorOptions ParseOptions(string[] args)
  {
    string name = "Polar H10 Simulator";
    ushort heartRate = 72;
    byte battery = 100;
    int frameSize = PolarH10SimulatorFrameCodec.DefaultFrameSize;
    int interval = 1;
    string seedId = "simulated-run";
    bool includeSeed = true;
    bool mtuWriteWithoutResponseOnly = false;
    int delayMs = 0;
    int dropEvery = 0;
    int disconnectAfter = 0;
    var values = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase);
    for (int index = 0; index < args.Length; index++)
    {
      string argument = args[index];
      if (!argument.StartsWith("--", StringComparison.Ordinal)) throw new FormatException($"Unknown argument '{argument}'.");
      string key = argument[2..];
      if (key is "no-seed") { includeSeed = false; continue; }
      if (key is "mtu-write-without-response-only") { mtuWriteWithoutResponseOnly = true; continue; }
      if (key is "help" or "h") continue;
      if (++index >= args.Length || args[index].StartsWith("--", StringComparison.Ordinal))
        throw new FormatException($"Option '--{key}' requires a value.");
      values[key] = args[index];
    }
    if (values.TryGetValue("name", out string? value)) name = value!;
    if (values.TryGetValue("heart-rate", out value)) heartRate = ushort.Parse(value!);
    if (values.TryGetValue("battery", out value)) battery = byte.Parse(value!);
    if (values.TryGetValue("frame-size", out value)) frameSize = int.Parse(value!);
    if (values.TryGetValue("interval", out value)) interval = int.Parse(value!);
    if (values.TryGetValue("seed-id", out value)) seedId = value!;
    if (values.TryGetValue("notification-delay-ms", out value)) delayMs = int.Parse(value!);
    if (values.TryGetValue("drop-every", out value)) dropEvery = int.Parse(value!);
    if (values.TryGetValue("disconnect-after", out value)) disconnectAfter = int.Parse(value!);
    string? unknownOption = values.Keys.FirstOrDefault(static key => !IsKnownOption(key));
    if (unknownOption is not null)
      throw new FormatException($"Unknown option '--{unknownOption}'.");
    var options = new PolarH10SimulatorOptions
    {
      DeviceName = name,
      HeartRate = heartRate,
      BatteryPercent = battery,
      FrameSize = frameSize,
      IntervalSeconds = interval,
      SeedExerciseId = seedId,
      IncludeSeedRecording = includeSeed,
      NotificationDelay = TimeSpan.FromMilliseconds(delayMs),
      DropEveryNthNotification = dropEvery,
      DisconnectAfterNotifications = disconnectAfter,
      MtuWriteWithoutResponseOnly = mtuWriteWithoutResponseOnly,
    };
    options.Validate();
    return options;
  }

  private static void PrintUsage()
  {
    Console.WriteLine("Polar H10 BLE peripheral simulator");
    Console.WriteLine("Run on a Windows host whose Bluetooth adapter supports GATT peripheral advertising:");
    Console.WriteLine("  dotnet run --project tools/PolarH10Simulator --framework net10.0-windows10.0.22621.0 -- [options]");
    Console.WriteLine("Options: --name <text> --heart-rate <bpm> --battery <0-100> --frame-size <3-509>");
    Console.WriteLine("         --interval <1|5> --seed-id <id> --no-seed");
    Console.WriteLine("         --notification-delay-ms <n> --drop-every <n> --disconnect-after <n>");
    Console.WriteLine("         --mtu-write-without-response-only");
  }

  private static bool IsKnownOption(string key) => key is
    "name" or "heart-rate" or "battery" or "frame-size" or "interval" or "seed-id" or
    "notification-delay-ms" or "drop-every" or "disconnect-after";
}
