using Xunit;

// Each browser-test class owns an isolated gateway, port, and database copied from one migrated
// template. Three-way parallelism is the stable saturation point on the validation workstation;
// a fourth concurrent browser and gateway increases wall time through contention and causes
// otherwise-ready Blazor pages to miss their focused readiness assertions.
[assembly: CollectionBehavior(MaxParallelThreads = 3)]
