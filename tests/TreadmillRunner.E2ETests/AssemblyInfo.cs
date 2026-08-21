using Xunit;

// Each browser-test class owns an isolated gateway, port, and database copied from one migrated
// template. Two-way parallelism is the stable saturation point on the validation workstation;
// additional concurrent browsers and gateways increase wall time through contention and cause
// otherwise-ready Blazor pages to miss their focused readiness assertions.
[assembly: CollectionBehavior(MaxParallelThreads = 2)]
