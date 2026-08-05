using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;

var logger = NullLogger.Instance;
logger.LogInformation("Sample consumer fixture initialized.");
Console.WriteLine("sample-consumer");
