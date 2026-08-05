using Xunit;

// Each browser-test class hosts a gateway fixture. Serial execution prevents their migration
// bootstraps from contending for the same design-project build outputs.
[assembly: CollectionBehavior(DisableTestParallelization = true)]
