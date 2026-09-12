#if WINDOWS
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Foundation;
using Windows.Storage.Streams;

namespace TreadmillRunner.PolarH10Simulator;

/// <summary>
/// Thin Windows GATT-peripheral adapter. It deliberately contains no treadmill
/// references; all behavior comes from PolarH10SimulatorStateMachine.
/// </summary>
public sealed class WindowsPolarH10Peripheral(PolarH10SimulatorStateMachine stateMachine) : IAsyncDisposable
{
  private readonly PolarH10SimulatorStateMachine _stateMachine = stateMachine ?? throw new ArgumentNullException(nameof(stateMachine));
  private readonly SemaphoreSlim _writeGate = new(1, 1);
  private readonly CancellationTokenSource _lifetime = new();
  private readonly object _handlerSync = new();
  private TaskCompletionSource _handlersDrained = CompletedSignal();
  private int _activePftpHandlers;
  private GattServiceProvider? _heartRateProvider;
  private GattServiceProvider? _batteryProvider;
  private GattServiceProvider? _polarProvider;
  private GattLocalCharacteristic? _heartRateCharacteristic;
  private GattLocalCharacteristic? _batteryCharacteristic;
  private GattLocalCharacteristic? _mtuCharacteristic;
  private GattLocalCharacteristic? _d2hCharacteristic;
  private GattLocalCharacteristic? _h2dCharacteristic;
  private Task? _heartRateLoop;
  private int _acceptCallbacks;
  private int _started;

  public async Task StartAsync(CancellationToken cancellationToken = default)
  {
    if (Interlocked.Exchange(ref _started, 1) != 0) throw new InvalidOperationException("The simulator is already started.");
    try
    {
      _stateMachine.BeginConnection();
      _heartRateProvider = await CreateProviderAsync(PolarH10SimulatorUuids.HeartRateService, cancellationToken).ConfigureAwait(false);
      _heartRateCharacteristic = await CreateCharacteristicAsync(
        _heartRateProvider!.Service,
        PolarH10SimulatorUuids.HeartRateMeasurement,
        GattCharacteristicProperties.Read | GattCharacteristicProperties.Notify,
        "Heart Rate Measurement",
        _stateMachine.PeekHeartRateMeasurement(),
        cancellationToken).ConfigureAwait(false);

      _batteryProvider = await CreateProviderAsync(PolarH10SimulatorUuids.BatteryService, cancellationToken).ConfigureAwait(false);
      _batteryCharacteristic = await CreateCharacteristicAsync(
        _batteryProvider!.Service,
        PolarH10SimulatorUuids.BatteryLevel,
        GattCharacteristicProperties.Read | GattCharacteristicProperties.Notify,
        "Battery Level",
        [_stateMachine.Options.BatteryPercent],
        cancellationToken).ConfigureAwait(false);

      _polarProvider = await CreateProviderAsync(PolarH10SimulatorUuids.PolarPftpService, cancellationToken).ConfigureAwait(false);
      _mtuCharacteristic = await CreateCharacteristicAsync(
        _polarProvider!.Service,
        PolarH10SimulatorUuids.PolarPftpMtu,
        MtuProperties(_stateMachine.Options.MtuWriteWithoutResponseOnly),
        "Polar PFTP MTU",
        [(byte)_stateMachine.Options.FrameSize],
        cancellationToken).ConfigureAwait(false);
      _d2hCharacteristic = await CreateCharacteristicAsync(
        _polarProvider!.Service,
        PolarH10SimulatorUuids.PolarPftpDeviceToHost,
        GattCharacteristicProperties.Read | GattCharacteristicProperties.Notify,
        "Polar PFTP device to host",
        Array.Empty<byte>(),
        cancellationToken).ConfigureAwait(false);
      _h2dCharacteristic = await CreateCharacteristicAsync(
        _polarProvider!.Service,
        PolarH10SimulatorUuids.PolarPftpHostToDevice,
        GattCharacteristicProperties.Read | GattCharacteristicProperties.WriteWithoutResponse,
        "Polar PFTP host to device",
        Array.Empty<byte>(),
        cancellationToken).ConfigureAwait(false);

      _mtuCharacteristic!.WriteRequested += OnPftpWriteRequested;
      _h2dCharacteristic!.WriteRequested += OnPftpWriteRequested;
      _mtuCharacteristic.ReadRequested += OnMtuReadRequested;
      _d2hCharacteristic!.ReadRequested += OnD2hReadRequested;
      _h2dCharacteristic.ReadRequested += OnH2dReadRequested;
      _heartRateCharacteristic!.ReadRequested += OnHeartRateReadRequested;
      _batteryCharacteristic!.ReadRequested += OnBatteryReadRequested;
      _mtuCharacteristic.SubscribedClientsChanged += OnSubscribedClientsChanged;
      _heartRateCharacteristic.SubscribedClientsChanged += OnSubscribedClientsChanged;
      Volatile.Write(ref _acceptCallbacks, 1);

      _heartRateProvider!.AdvertisementStatusChanged += OnAdvertisementStatusChanged;
      _batteryProvider!.AdvertisementStatusChanged += OnAdvertisementStatusChanged;
      _polarProvider!.AdvertisementStatusChanged += OnAdvertisementStatusChanged;
      StartAdvertising(_heartRateProvider!);
      StartAdvertising(_batteryProvider!);
      StartAdvertising(_polarProvider!);
      _heartRateLoop = HeartRateLoopAsync(_lifetime.Token);
    }
    catch
    {
      await StopAsync().ConfigureAwait(false);
      throw;
    }
  }

  public async Task StopAsync()
  {
    if (Interlocked.Exchange(ref _started, 0) == 0) return;
    Volatile.Write(ref _acceptCallbacks, 0);
    if (_heartRateProvider is not null) _heartRateProvider.StopAdvertising();
    if (_batteryProvider is not null) _batteryProvider.StopAdvertising();
    if (_polarProvider is not null) _polarProvider.StopAdvertising();
    Unsubscribe(_mtuCharacteristic);
    Unsubscribe(_d2hCharacteristic);
    Unsubscribe(_h2dCharacteristic);
    Unsubscribe(_heartRateCharacteristic);
    Unsubscribe(_batteryCharacteristic);
    _lifetime.Cancel();
    if (_heartRateLoop is not null)
    {
      try { await _heartRateLoop.ConfigureAwait(false); }
      catch (OperationCanceledException) when (_lifetime.IsCancellationRequested) { }
    }
    await WaitForPftpHandlersAsync().ConfigureAwait(false);
    _heartRateProvider = null;
    _batteryProvider = null;
    _polarProvider = null;
  }

  public async ValueTask DisposeAsync()
  {
    await StopAsync().ConfigureAwait(false);
    _writeGate.Dispose();
    _lifetime.Dispose();
  }

  private async Task HandlePftpWriteAsync(byte[] packet)
  {
    await _writeGate.WaitAsync(_lifetime.Token).ConfigureAwait(false);
    try
    {
      IReadOnlyList<PolarH10SimulatorEmission> emissions = _stateMachine.AcceptHostPacket(packet);
      foreach (PolarH10SimulatorEmission emission in emissions)
      {
        if (!emission.Dropped)
        {
          await DelayAsync(_stateMachine.Options.NotificationDelay, _lifetime.Token).ConfigureAwait(false);
          if (emission.Characteristic == PolarH10SimulatorCharacteristic.Mtu && _mtuCharacteristic is not null)
            await NotifyAsync(_mtuCharacteristic, emission.Value, _lifetime.Token).ConfigureAwait(false);
          else if (emission.Characteristic == PolarH10SimulatorCharacteristic.DeviceToHost && _d2hCharacteristic is not null)
            await NotifyAsync(_d2hCharacteristic, emission.Value, _lifetime.Token).ConfigureAwait(false);
        }
        if (emission.DisconnectRequested)
        {
          CloseSubscribedClients(_mtuCharacteristic);
          CloseSubscribedClients(_d2hCharacteristic);
          break;
        }
      }
    }
    finally
    {
      _writeGate.Release();
    }
  }

  private async Task HeartRateLoopAsync(CancellationToken cancellationToken)
  {
    using var timer = new PeriodicTimer(TimeSpan.FromSeconds(1));
    while (await timer.WaitForNextTickAsync(cancellationToken).ConfigureAwait(false))
    {
      await _writeGate.WaitAsync(cancellationToken).ConfigureAwait(false);
      PolarH10SimulatorEmission emission;
      try
      {
        if (_stateMachine.DisconnectRequested) continue;
        emission = _stateMachine.CreateHeartRateNotification();
      }
      finally { _writeGate.Release(); }
      if (emission.Dropped || _heartRateCharacteristic is null) continue;
      await DelayAsync(_stateMachine.Options.NotificationDelay, cancellationToken).ConfigureAwait(false);
      await NotifyAsync(_heartRateCharacteristic, emission.Value, cancellationToken).ConfigureAwait(false);
      if (emission.DisconnectRequested)
      {
        CloseSubscribedClients(_heartRateCharacteristic);
      }
    }
  }

  private async void OnPftpWriteRequested(GattLocalCharacteristic _, GattWriteRequestedEventArgs args)
  {
    if (!TryEnterPftpHandler()) return;
    Deferral? deferral = null;
    GattWriteRequest? request = null;
    try
    {
      deferral = args.GetDeferral();
      request = await args.GetRequestAsync().AsTask().ConfigureAwait(false);
      if (request is null) return;
      if (request.Offset != 0)
      {
        if (request.Option == GattWriteOption.WriteWithResponse)
          request.RespondWithProtocolError(GattProtocolError.InvalidOffset);
        return;
      }
      byte[] packet = ReadBuffer(request.Value);
      if (request.Option == GattWriteOption.WriteWithResponse) request.Respond();
      await HandlePftpWriteAsync(packet).ConfigureAwait(false);
    }
    catch (OperationCanceledException) when (_lifetime.IsCancellationRequested) { }
    catch (Exception exception)
    {
      Console.Error.WriteLine($"PFTP write handling failed: {exception.Message}");
      try
      {
        if (request?.Option == GattWriteOption.WriteWithResponse)
          request.RespondWithProtocolError(GattProtocolError.UnlikelyError);
      }
      catch { }
    }
    finally
    {
      deferral?.Complete();
      ExitPftpHandler();
    }
  }

  private bool TryEnterPftpHandler()
  {
    lock (_handlerSync)
    {
      if (Volatile.Read(ref _acceptCallbacks) == 0) return false;
      if (_activePftpHandlers++ == 0)
        _handlersDrained = new(TaskCreationOptions.RunContinuationsAsynchronously);
      return true;
    }
  }

  private void ExitPftpHandler()
  {
    TaskCompletionSource? completed = null;
    lock (_handlerSync)
    {
      if (--_activePftpHandlers == 0) completed = _handlersDrained;
    }
    completed?.TrySetResult();
  }

  private Task WaitForPftpHandlersAsync()
  {
    lock (_handlerSync) return _handlersDrained.Task;
  }

  private static TaskCompletionSource CompletedSignal()
  {
    var signal = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    signal.SetResult();
    return signal;
  }

  private async void OnMtuReadRequested(GattLocalCharacteristic _, GattReadRequestedEventArgs args) =>
    await RespondToReadAsync(args, [(byte)_stateMachine.Options.FrameSize]).ConfigureAwait(false);

  private async void OnD2hReadRequested(GattLocalCharacteristic _, GattReadRequestedEventArgs args) =>
    await RespondToReadAsync(args, Array.Empty<byte>()).ConfigureAwait(false);

  private async void OnH2dReadRequested(GattLocalCharacteristic _, GattReadRequestedEventArgs args) =>
    await RespondToReadAsync(args, Array.Empty<byte>()).ConfigureAwait(false);

  private async void OnHeartRateReadRequested(GattLocalCharacteristic _, GattReadRequestedEventArgs args) =>
    await RespondToReadAsync(args, _stateMachine.PeekHeartRateMeasurement()).ConfigureAwait(false);

  private async void OnBatteryReadRequested(GattLocalCharacteristic _, GattReadRequestedEventArgs args) =>
    await RespondToReadAsync(args, [_stateMachine.Options.BatteryPercent]).ConfigureAwait(false);

  private static async Task RespondToReadAsync(GattReadRequestedEventArgs args, byte[] value)
  {
    Deferral deferral = args.GetDeferral();
    try
    {
      GattReadRequest? request = await args.GetRequestAsync().AsTask().ConfigureAwait(false);
      if (request is null) return;
      if (request.Offset > (uint)value.Length)
      {
        request.RespondWithProtocolError(GattProtocolError.InvalidOffset);
        return;
      }
      int offset = checked((int)request.Offset);
      int requestedLength = checked((int)request.Length);
      int length = requestedLength == 0 ? value.Length - offset : Math.Min(requestedLength, value.Length - offset);
      using var writer = new DataWriter();
      writer.WriteBytes(value.AsSpan(offset, length).ToArray());
      request.RespondWithValue(writer.DetachBuffer());
    }
    catch
    {
      try { (await args.GetRequestAsync().AsTask().ConfigureAwait(false))?.RespondWithProtocolError(GattProtocolError.UnlikelyError); } catch { }
    }
    finally
    {
      deferral.Complete();
    }
  }

  private static async Task<GattServiceProvider> CreateProviderAsync(Guid serviceUuid, CancellationToken cancellationToken)
  {
    GattServiceProviderResult result = await GattServiceProvider.CreateAsync(serviceUuid).AsTask(cancellationToken).ConfigureAwait(false);
    return result.ServiceProvider ?? throw new InvalidOperationException($"Windows could not create GATT service {serviceUuid}.");
  }

  private static async Task<GattLocalCharacteristic> CreateCharacteristicAsync(
    GattLocalService service,
    Guid uuid,
    GattCharacteristicProperties properties,
    string description,
    byte[] staticValue,
    CancellationToken cancellationToken)
  {
    var parameters = new GattLocalCharacteristicParameters
    {
      CharacteristicProperties = properties,
      UserDescription = description,
    };
    if (staticValue.Length > 0)
    {
      using var writer = new DataWriter();
      writer.WriteBytes(staticValue);
      parameters.StaticValue = writer.DetachBuffer();
    }
    GattLocalCharacteristicResult result = await service.CreateCharacteristicAsync(uuid, parameters)
      .AsTask(cancellationToken).ConfigureAwait(false);
    return result.Characteristic ?? throw new InvalidOperationException($"Windows could not create GATT characteristic {uuid}.");
  }

  private static void StartAdvertising(GattServiceProvider provider)
  {
    provider.StartAdvertising(new GattServiceProviderAdvertisingParameters
    {
      IsConnectable = true,
      IsDiscoverable = true,
    });
  }

  private static GattCharacteristicProperties MtuProperties(bool writeWithoutResponseOnly) =>
    GattCharacteristicProperties.Read |
    GattCharacteristicProperties.WriteWithoutResponse |
    GattCharacteristicProperties.Notify |
    (writeWithoutResponseOnly ? 0 : GattCharacteristicProperties.Write);

  private static async Task NotifyAsync(GattLocalCharacteristic characteristic, byte[] value, CancellationToken cancellationToken)
  {
    using var writer = new DataWriter();
    writer.WriteBytes(value);
    await characteristic.NotifyValueAsync(writer.DetachBuffer()).AsTask(cancellationToken).ConfigureAwait(false);
  }

  private static async Task DelayAsync(TimeSpan delay, CancellationToken cancellationToken)
  {
    if (delay > TimeSpan.Zero) await Task.Delay(delay, cancellationToken).ConfigureAwait(false);
  }

  private static byte[] ReadBuffer(IBuffer buffer)
  {
    using DataReader reader = DataReader.FromBuffer(buffer);
    var value = new byte[reader.UnconsumedBufferLength];
    reader.ReadBytes(value);
    return value;
  }

  private static void CloseSubscribedClients(GattLocalCharacteristic? characteristic)
  {
    if (characteristic is null) return;
    foreach (GattSubscribedClient client in characteristic.SubscribedClients.ToArray())
    {
      try { client.Session.Dispose(); } catch { }
    }
  }

  private void Unsubscribe(GattLocalCharacteristic? characteristic)
  {
    if (characteristic is null) return;
    if (characteristic == _mtuCharacteristic || characteristic == _h2dCharacteristic)
      characteristic.WriteRequested -= OnPftpWriteRequested;
    characteristic.ReadRequested -= OnMtuReadRequested;
    characteristic.ReadRequested -= OnD2hReadRequested;
    characteristic.ReadRequested -= OnH2dReadRequested;
    characteristic.ReadRequested -= OnHeartRateReadRequested;
    characteristic.ReadRequested -= OnBatteryReadRequested;
    characteristic.SubscribedClientsChanged -= OnSubscribedClientsChanged;
  }

  private async void OnSubscribedClientsChanged(GattLocalCharacteristic sender, object _)
  {
    if (!TryEnterPftpHandler()) return;
    bool acquired = false;
    try
    {
      await _writeGate.WaitAsync(_lifetime.Token).ConfigureAwait(false);
      acquired = true;
      if (sender.SubscribedClients.Count > 0 && _stateMachine.DisconnectRequested)
        _stateMachine.BeginConnection();
    }
    catch (OperationCanceledException) when (_lifetime.IsCancellationRequested) { }
    catch (Exception exception)
    {
      Console.Error.WriteLine($"Subscribed-client handling failed: {exception.Message}");
    }
    finally
    {
      if (acquired) _writeGate.Release();
      ExitPftpHandler();
    }
  }

  private void OnAdvertisementStatusChanged(GattServiceProvider sender, GattServiceProviderAdvertisementStatusChangedEventArgs args) =>
    Console.WriteLine($"Advertisement {sender.Service.Uuid}: {args.Status}");
}
#endif
