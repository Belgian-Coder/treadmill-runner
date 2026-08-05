namespace TreadmillRunner.Core.Control;

public sealed record ControlLease(
    Guid Id,
    string HolderId,
    DateTimeOffset AcquiredAt,
    DateTimeOffset ExpiresAt);
