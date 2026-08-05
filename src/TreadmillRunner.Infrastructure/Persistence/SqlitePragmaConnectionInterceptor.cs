using System.Data.Common;
using Microsoft.EntityFrameworkCore.Diagnostics;

namespace TreadmillRunner.Infrastructure.Persistence;

internal sealed class SqlitePragmaConnectionInterceptor : DbConnectionInterceptor
{
  public static SqlitePragmaConnectionInterceptor Instance { get; } = new();

  private static readonly string[] Pragmas =
  [
    "PRAGMA foreign_keys=ON;",
    "PRAGMA busy_timeout=5000;",
  ];

  private SqlitePragmaConnectionInterceptor()
  {
  }

  public override void ConnectionOpened(DbConnection connection, ConnectionEndEventData eventData) =>
    ApplyPragmas(connection);

  public override async Task ConnectionOpenedAsync(
    DbConnection connection,
    ConnectionEndEventData eventData,
    CancellationToken cancellationToken = default) =>
    await ApplyPragmasAsync(connection, cancellationToken).ConfigureAwait(false);

  private static void ApplyPragmas(DbConnection connection)
  {
    foreach (var pragma in Pragmas)
    {
      using var command = connection.CreateCommand();
      command.CommandText = pragma;
      command.ExecuteNonQuery();
    }
  }

  private static async Task ApplyPragmasAsync(
    DbConnection connection,
    CancellationToken cancellationToken)
  {
    foreach (var pragma in Pragmas)
    {
      await using var command = connection.CreateCommand();
      command.CommandText = pragma;
      await command.ExecuteNonQueryAsync(cancellationToken).ConfigureAwait(false);
    }
  }
}
