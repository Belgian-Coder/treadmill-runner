#!/usr/bin/env python3
"""Generate and validate the fixed .NET 10 EF Core feature-sliced benchmark fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import benchmark_common as common


ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SUITE = (
    ROOT
    / "automations"
    / "local-ai-benchmark-workflow"
    / "suites"
    / "dotnet10-feature-sliced-efcore-project.json"
)


FILES: dict[str, str] = {
    "src/InventoryService.Api/InventoryService.Api.csproj": """<Project Sdk=\"Microsoft.NET.Sdk.Web\">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include=\"Microsoft.EntityFrameworkCore.Sqlite\" Version=\"10.0.0\" />
  </ItemGroup>
</Project>
""",
    "src/InventoryService.Api/Program.cs": """using InventoryService.Api.Data;
using InventoryService.Api.Features.Products;
using InventoryService.Api.Features.Reports;
using InventoryService.Api.Features.Reservations;
using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddProblemDetails();
builder.Services.AddHealthChecks();
builder.Services.AddDbContext<InventoryDbContext>(options =>
    options.UseSqlite(builder.Configuration.GetConnectionString(\"Inventory\") ?? \"Data Source=inventory.db\"));
builder.Services.AddScoped<ProductService>();
builder.Services.AddScoped<ReservationService>();
builder.Services.AddScoped<ReportService>();

var app = builder.Build();

app.UseExceptionHandler();
app.MapHealthChecks(\"/health\");
app.MapProductEndpoints();
app.MapReservationEndpoints();
app.MapReportEndpoints();

app.Run();

public partial class Program
{
}
""",
    "src/InventoryService.Api/Data/InventoryEntities.cs": """namespace InventoryService.Api.Data;

public sealed class Product
{
    public int Id { get; set; }
    public string Sku { get; set; } = string.Empty;
    public string Name { get; set; } = string.Empty;
    public int QuantityOnHand { get; set; }
    public int ReorderThreshold { get; set; }
    public List<Reservation> Reservations { get; } = [];
}

public sealed class Reservation
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public int ProductId { get; set; }
    public Product Product { get; set; } = null!;
    public string OrderNumber { get; set; } = string.Empty;
    public string IdempotencyKey { get; set; } = string.Empty;
    public int Quantity { get; set; }
    public ReservationStatus Status { get; set; } = ReservationStatus.Held;
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
    public DateTimeOffset? CompletedAt { get; set; }
}

public enum ReservationStatus
{
    Held = 0,
    Fulfilled = 1,
    Canceled = 2
}
""",
    "src/InventoryService.Api/Data/InventoryDbContext.cs": """using Microsoft.EntityFrameworkCore;

namespace InventoryService.Api.Data;

public sealed class InventoryDbContext(DbContextOptions<InventoryDbContext> options) : DbContext(options)
{
    public DbSet<Product> Products => Set<Product>();
    public DbSet<Reservation> Reservations => Set<Reservation>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<Product>(entity =>
        {
            entity.HasIndex(product => product.Sku).IsUnique();
            entity.Property(product => product.Sku).HasMaxLength(64).IsRequired();
            entity.Property(product => product.Name).HasMaxLength(160).IsRequired();
            entity.HasMany(product => product.Reservations)
                .WithOne(reservation => reservation.Product)
                .HasForeignKey(reservation => reservation.ProductId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        modelBuilder.Entity<Reservation>(entity =>
        {
            entity.HasIndex(reservation => reservation.IdempotencyKey).IsUnique();
            entity.Property(reservation => reservation.IdempotencyKey).HasMaxLength(128).IsRequired();
            entity.Property(reservation => reservation.OrderNumber).HasMaxLength(80).IsRequired();
            entity.Property(reservation => reservation.Status).HasConversion<string>().HasMaxLength(32);
        });
    }
}
""",
    "src/InventoryService.Api/Features/Products/ProductContracts.cs": """using InventoryService.Api.Data;

namespace InventoryService.Api.Features.Products;

public sealed record CreateProductRequest(string Sku, string Name, int QuantityOnHand, int ReorderThreshold);

public sealed record ProductResponse(string Sku, string Name, int QuantityOnHand, int ReorderThreshold, int AvailableQuantity)
{
    public static ProductResponse From(Product product, int availableQuantity) =>
        new(product.Sku, product.Name, product.QuantityOnHand, product.ReorderThreshold, availableQuantity);
}
""",
    "src/InventoryService.Api/Features/Products/ProductService.cs": """using InventoryService.Api.Data;
using Microsoft.EntityFrameworkCore;

namespace InventoryService.Api.Features.Products;

public sealed class ProductService(InventoryDbContext db)
{
    public async Task<(ProductResponse? Product, string? Error, int StatusCode)> CreateAsync(
        CreateProductRequest request,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(request.Sku) || string.IsNullOrWhiteSpace(request.Name))
        {
            return (null, \"SKU and name are required.\", StatusCodes.Status400BadRequest);
        }

        if (request.QuantityOnHand < 0 || request.ReorderThreshold < 0)
        {
            return (null, \"Quantities cannot be negative.\", StatusCodes.Status400BadRequest);
        }

        var sku = request.Sku.Trim().ToUpperInvariant();
        if (await db.Products.AnyAsync(product => product.Sku == sku, cancellationToken))
        {
            return (null, \"SKU already exists.\", StatusCodes.Status409Conflict);
        }

        var product = new Product
        {
            Sku = sku,
            Name = request.Name.Trim(),
            QuantityOnHand = request.QuantityOnHand,
            ReorderThreshold = request.ReorderThreshold
        };
        db.Products.Add(product);
        await db.SaveChangesAsync(cancellationToken);
        return (ProductResponse.From(product, product.QuantityOnHand), null, StatusCodes.Status201Created);
    }

    public async Task<IReadOnlyList<ProductResponse>> ListAsync(CancellationToken cancellationToken)
    {
        var products = await db.Products
            .Include(product => product.Reservations)
            .OrderBy(product => product.Sku)
            .ToListAsync(cancellationToken);
        return products
            .Select(product => ProductResponse.From(product, AvailableQuantity(product)))
            .ToList();
    }

    public static int AvailableQuantity(Product product) =>
        product.QuantityOnHand - product.Reservations
            .Where(reservation => reservation.Status == ReservationStatus.Held)
            .Sum(reservation => reservation.Quantity);
}
""",
    "src/InventoryService.Api/Features/Products/ProductEndpoints.cs": """using Microsoft.AspNetCore.Http.HttpResults;

namespace InventoryService.Api.Features.Products;

public static class ProductEndpoints
{
    public static RouteGroupBuilder MapProductEndpoints(this IEndpointRouteBuilder routes)
    {
        var group = routes.MapGroup(\"/products\").WithTags(\"Products\");

        group.MapPost(\"/\", async Task<IResult> (
            CreateProductRequest request,
            ProductService service,
            CancellationToken cancellationToken) =>
        {
            var result = await service.CreateAsync(request, cancellationToken);
            return result.Product is not null
                ? TypedResults.Created($\"/products/{result.Product.Sku}\", result.Product)
                : TypedResults.Problem(title: result.Error, statusCode: result.StatusCode);
        });

        group.MapGet(\"/\", async Task<Ok<IReadOnlyList<ProductResponse>>> (
            ProductService service,
            CancellationToken cancellationToken) =>
            TypedResults.Ok(await service.ListAsync(cancellationToken)));

        return group;
    }
}
""",
    "src/InventoryService.Api/Features/Reservations/ReservationContracts.cs": """using InventoryService.Api.Data;

namespace InventoryService.Api.Features.Reservations;

public sealed record CreateReservationRequest(string Sku, string OrderNumber, int Quantity);

public sealed record ReservationResponse(Guid Id, string Sku, string OrderNumber, int Quantity, string Status, string IdempotencyKey)
{
    public static ReservationResponse From(Reservation reservation) =>
        new(
            reservation.Id,
            reservation.Product.Sku,
            reservation.OrderNumber,
            reservation.Quantity,
            reservation.Status.ToString(),
            reservation.IdempotencyKey);
}
""",
    "src/InventoryService.Api/Features/Reservations/ReservationService.cs": """using InventoryService.Api.Data;
using InventoryService.Api.Features.Products;
using Microsoft.EntityFrameworkCore;

namespace InventoryService.Api.Features.Reservations;

public sealed class ReservationService(InventoryDbContext db)
{
    public async Task<(ReservationResponse? Reservation, string? Error, int StatusCode)> CreateAsync(
        string? idempotencyKey,
        CreateReservationRequest request,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(idempotencyKey))
        {
            return (null, \"Idempotency-Key header is required.\", StatusCodes.Status400BadRequest);
        }

        var existing = await db.Reservations
            .Include(reservation => reservation.Product)
            .SingleOrDefaultAsync(reservation => reservation.IdempotencyKey == idempotencyKey, cancellationToken);
        if (existing is not null)
        {
            return (ReservationResponse.From(existing), null, StatusCodes.Status200OK);
        }

        if (request.Quantity <= 0 || string.IsNullOrWhiteSpace(request.Sku) || string.IsNullOrWhiteSpace(request.OrderNumber))
        {
            return (null, \"SKU, order number, and positive quantity are required.\", StatusCodes.Status400BadRequest);
        }

        var sku = request.Sku.Trim().ToUpperInvariant();
        var product = await db.Products
            .Include(item => item.Reservations)
            .SingleOrDefaultAsync(item => item.Sku == sku, cancellationToken);
        if (product is null)
        {
            return (null, \"Product was not found.\", StatusCodes.Status404NotFound);
        }

        if (ProductService.AvailableQuantity(product) < request.Quantity)
        {
            return (null, \"Insufficient available inventory.\", StatusCodes.Status409Conflict);
        }

        var reservation = new Reservation
        {
            Product = product,
            OrderNumber = request.OrderNumber.Trim(),
            Quantity = request.Quantity,
            IdempotencyKey = idempotencyKey.Trim()
        };
        db.Reservations.Add(reservation);
        await db.SaveChangesAsync(cancellationToken);
        return (ReservationResponse.From(reservation), null, StatusCodes.Status201Created);
    }

    public Task<(ReservationResponse? Reservation, string? Error, int StatusCode)> FulfillAsync(
        Guid id,
        CancellationToken cancellationToken) =>
        CompleteAsync(id, ReservationStatus.Fulfilled, cancellationToken);

    public Task<(ReservationResponse? Reservation, string? Error, int StatusCode)> CancelAsync(
        Guid id,
        CancellationToken cancellationToken) =>
        CompleteAsync(id, ReservationStatus.Canceled, cancellationToken);

    private async Task<(ReservationResponse? Reservation, string? Error, int StatusCode)> CompleteAsync(
        Guid id,
        ReservationStatus targetStatus,
        CancellationToken cancellationToken)
    {
        var reservation = await db.Reservations
            .Include(item => item.Product)
            .SingleOrDefaultAsync(item => item.Id == id, cancellationToken);
        if (reservation is null)
        {
            return (null, \"Reservation was not found.\", StatusCodes.Status404NotFound);
        }

        if (reservation.Status != ReservationStatus.Held)
        {
            return (ReservationResponse.From(reservation), null, StatusCodes.Status200OK);
        }

        reservation.Status = targetStatus;
        reservation.CompletedAt = DateTimeOffset.UtcNow;
        if (targetStatus == ReservationStatus.Fulfilled)
        {
            reservation.Product.QuantityOnHand -= reservation.Quantity;
        }

        await db.SaveChangesAsync(cancellationToken);
        return (ReservationResponse.From(reservation), null, StatusCodes.Status200OK);
    }
}
""",
    "src/InventoryService.Api/Features/Reservations/ReservationEndpoints.cs": """namespace InventoryService.Api.Features.Reservations;

public static class ReservationEndpoints
{
    public static RouteGroupBuilder MapReservationEndpoints(this IEndpointRouteBuilder routes)
    {
        var group = routes.MapGroup(\"/reservations\").WithTags(\"Reservations\");

        group.MapPost(\"/\", async Task<IResult> (
            HttpRequest httpRequest,
            CreateReservationRequest request,
            ReservationService service,
            CancellationToken cancellationToken) =>
        {
            var result = await service.CreateAsync(
                httpRequest.Headers[\"Idempotency-Key\"].FirstOrDefault(),
                request,
                cancellationToken);
            return result.Reservation is not null
                ? (result.StatusCode == StatusCodes.Status201Created
                    ? TypedResults.Created($\"/reservations/{result.Reservation.Id}\", result.Reservation)
                    : TypedResults.Ok(result.Reservation))
                : TypedResults.Problem(title: result.Error, statusCode: result.StatusCode);
        });

        group.MapPost(\"/{id:guid}/fulfill\", async Task<IResult> (
            Guid id,
            ReservationService service,
            CancellationToken cancellationToken) =>
        {
            var result = await service.FulfillAsync(id, cancellationToken);
            return result.Reservation is not null
                ? TypedResults.Ok(result.Reservation)
                : TypedResults.Problem(title: result.Error, statusCode: result.StatusCode);
        });

        group.MapPost(\"/{id:guid}/cancel\", async Task<IResult> (
            Guid id,
            ReservationService service,
            CancellationToken cancellationToken) =>
        {
            var result = await service.CancelAsync(id, cancellationToken);
            return result.Reservation is not null
                ? TypedResults.Ok(result.Reservation)
                : TypedResults.Problem(title: result.Error, statusCode: result.StatusCode);
        });

        return group;
    }
}
""",
    "src/InventoryService.Api/Features/Reports/ReportService.cs": """using InventoryService.Api.Data;
using InventoryService.Api.Features.Products;
using Microsoft.EntityFrameworkCore;

namespace InventoryService.Api.Features.Reports;

public sealed record LowStockProductResponse(string Sku, string Name, int AvailableQuantity, int ReorderThreshold);

public sealed class ReportService(InventoryDbContext db)
{
    public async Task<IReadOnlyList<LowStockProductResponse>> LowStockAsync(CancellationToken cancellationToken)
    {
        var products = await db.Products
            .Include(product => product.Reservations)
            .OrderBy(product => product.Sku)
            .ToListAsync(cancellationToken);

        return products
            .Select(product => new LowStockProductResponse(
                product.Sku,
                product.Name,
                ProductService.AvailableQuantity(product),
                product.ReorderThreshold))
            .Where(product => product.AvailableQuantity <= product.ReorderThreshold)
            .ToList();
    }
}
""",
    "src/InventoryService.Api/Features/Reports/ReportEndpoints.cs": """using Microsoft.AspNetCore.Http.HttpResults;

namespace InventoryService.Api.Features.Reports;

public static class ReportEndpoints
{
    public static RouteGroupBuilder MapReportEndpoints(this IEndpointRouteBuilder routes)
    {
        var group = routes.MapGroup(\"/reports\").WithTags(\"Reports\");

        group.MapGet(\"/low-stock\", async Task<Ok<IReadOnlyList<LowStockProductResponse>>> (
            ReportService service,
            CancellationToken cancellationToken) =>
            TypedResults.Ok(await service.LowStockAsync(cancellationToken)));

        return group;
    }
}
""",
    "tests/InventoryService.Api.Tests/InventoryService.Api.Tests.csproj": """<Project Sdk=\"Microsoft.NET.Sdk\">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <IsPackable>false</IsPackable>
    <OutputType>Exe</OutputType>
    <TestingPlatformDotnetTestSupport>true</TestingPlatformDotnetTestSupport>
  </PropertyGroup>
  <ItemGroup>
    <ProjectReference Include=\"..\\..\\src\\InventoryService.Api\\InventoryService.Api.csproj\" />
  </ItemGroup>
  <ItemGroup>
    <PackageReference Include=\"Microsoft.AspNetCore.Mvc.Testing\" Version=\"10.0.0\" />
    <PackageReference Include=\"Microsoft.EntityFrameworkCore.Sqlite\" Version=\"10.0.0\" />
    <PackageReference Include=\"Microsoft.NET.Test.Sdk\" Version=\"18.6.0\" />
    <PackageReference Include=\"xunit.v3\" Version=\"3.2.2\" />
    <PackageReference Include=\"xunit.runner.visualstudio\" Version=\"3.1.5\" PrivateAssets=\"all\" />
  </ItemGroup>
  <ItemGroup>
    <Using Include=\"Xunit\" />
  </ItemGroup>
</Project>
""",
    "tests/InventoryService.Api.Tests/InventoryApiFactory.cs": """using System.Data;
using System.Data.Common;
using InventoryService.Api.Data;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace InventoryService.Api.Tests;

public sealed class InventoryApiFactory : WebApplicationFactory<Program>
{
    private readonly DbConnection connection = new SqliteConnection(\"DataSource=:memory:\");

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        if (connection.State != ConnectionState.Open)
        {
            connection.Open();
        }
        builder.ConfigureServices(services =>
        {
            var descriptor = services.SingleOrDefault(
                service => service.ServiceType == typeof(DbContextOptions<InventoryDbContext>));
            if (descriptor is not null)
            {
                services.Remove(descriptor);
            }

            services.AddSingleton(connection);
            services.AddDbContext<InventoryDbContext>((provider, options) =>
                options.UseSqlite(provider.GetRequiredService<DbConnection>()));
        });
    }

    public async Task ResetDatabaseAsync()
    {
        await using var scope = Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<InventoryDbContext>();
        await db.Database.EnsureDeletedAsync();
        await db.Database.EnsureCreatedAsync();
    }

    protected override void Dispose(bool disposing)
    {
        base.Dispose(disposing);
        if (disposing)
        {
            connection.Dispose();
        }
    }
}
""",
    "tests/InventoryService.Api.Tests/InventoryWorkflowTests.cs": """using System.Net;
using System.Net.Http.Json;
using System.Text.Json.Serialization;

namespace InventoryService.Api.Tests;

public sealed class InventoryWorkflowTests(InventoryApiFactory factory) : IClassFixture<InventoryApiFactory>
{
    [Fact]
    public async Task Create_and_list_products()
    {
        await factory.ResetDatabaseAsync();
        using var client = factory.CreateClient();

        var created = await client.PostAsJsonAsync(\"/products\", new
        {
            sku = \"abc-123\",
            name = \"Scanner\",
            quantityOnHand = 12,
            reorderThreshold = 3
        }, TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.Created, created.StatusCode);

        var products = await client.GetFromJsonAsync<List<ProductResponseDto>>(
            \"/products\",
            TestContext.Current.CancellationToken);
        var product = Assert.Single(products!);
        Assert.Equal(\"ABC-123\", product.Sku);
        Assert.Equal(12, product.AvailableQuantity);
    }

    [Fact]
    public async Task Reservation_is_idempotent_and_reduces_available_quantity()
    {
        await factory.ResetDatabaseAsync();
        using var client = factory.CreateClient();
        await CreateProductAsync(client, \"SKU-1\", 10, 3);

        var first = await ReserveAsync(client, \"reserve-1\", \"SKU-1\", 4);
        var second = await ReserveAsync(client, \"reserve-1\", \"SKU-1\", 4);

        Assert.Equal(first.Id, second.Id);
        var products = await client.GetFromJsonAsync<List<ProductResponseDto>>(
            \"/products\",
            TestContext.Current.CancellationToken);
        Assert.Equal(6, Assert.Single(products!).AvailableQuantity);
    }

    [Fact]
    public async Task Reservation_rejects_insufficient_inventory()
    {
        await factory.ResetDatabaseAsync();
        using var client = factory.CreateClient();
        await CreateProductAsync(client, \"SKU-2\", 2, 1);

        using var request = new HttpRequestMessage(HttpMethod.Post, \"/reservations\");
        request.Headers.Add(\"Idempotency-Key\", \"too-many\");
        request.Content = JsonContent.Create(new { sku = \"SKU-2\", orderNumber = \"SO-2\", quantity = 3 });

        var response = await client.SendAsync(request, TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.Conflict, response.StatusCode);
    }

    [Fact]
    public async Task Cancel_and_fulfill_update_low_stock_report()
    {
        await factory.ResetDatabaseAsync();
        using var client = factory.CreateClient();
        await CreateProductAsync(client, \"SKU-3\", 10, 3);

        var held = await ReserveAsync(client, \"hold\", \"SKU-3\", 7);
        var lowStock = await client.GetFromJsonAsync<List<LowStockResponseDto>>(
            \"/reports/low-stock\",
            TestContext.Current.CancellationToken);
        Assert.Contains(lowStock!, item => item.Sku == \"SKU-3\" && item.AvailableQuantity == 3);

        var canceled = await client.PostAsync(
            $\"/reservations/{held.Id}/cancel\",
            content: null,
            TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, canceled.StatusCode);
        lowStock = await client.GetFromJsonAsync<List<LowStockResponseDto>>(
            \"/reports/low-stock\",
            TestContext.Current.CancellationToken);
        Assert.DoesNotContain(lowStock!, item => item.Sku == \"SKU-3\");

        var fulfilled = await ReserveAsync(client, \"fulfill\", \"SKU-3\", 7);
        var fulfillResponse = await client.PostAsync(
            $\"/reservations/{fulfilled.Id}/fulfill\",
            content: null,
            TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, fulfillResponse.StatusCode);
        lowStock = await client.GetFromJsonAsync<List<LowStockResponseDto>>(
            \"/reports/low-stock\",
            TestContext.Current.CancellationToken);
        Assert.Contains(lowStock!, item => item.Sku == \"SKU-3\" && item.AvailableQuantity == 3);
    }

    private static async Task CreateProductAsync(HttpClient client, string sku, int quantity, int threshold)
    {
        var response = await client.PostAsJsonAsync(\"/products\", new
        {
            sku,
            name = $\"Product {sku}\",
            quantityOnHand = quantity,
            reorderThreshold = threshold
        }, TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
    }

    private static async Task<ReservationResponseDto> ReserveAsync(HttpClient client, string key, string sku, int quantity)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, \"/reservations\");
        request.Headers.Add(\"Idempotency-Key\", key);
        request.Content = JsonContent.Create(new { sku, orderNumber = $\"SO-{key}\", quantity });
        var response = await client.SendAsync(request, TestContext.Current.CancellationToken);
        Assert.True(response.StatusCode is HttpStatusCode.Created or HttpStatusCode.OK, response.StatusCode.ToString());
        return (await response.Content.ReadFromJsonAsync<ReservationResponseDto>(
            TestContext.Current.CancellationToken))!;
    }

    private sealed record ProductResponseDto(
        [property: JsonPropertyName(\"sku\")] string Sku,
        [property: JsonPropertyName(\"availableQuantity\")] int AvailableQuantity);

    private sealed record ReservationResponseDto(
        [property: JsonPropertyName(\"id\")] Guid Id,
        [property: JsonPropertyName(\"status\")] string Status);

    private sealed record LowStockResponseDto(
        [property: JsonPropertyName(\"sku\")] string Sku,
        [property: JsonPropertyName(\"availableQuantity\")] int AvailableQuantity);
}
""",
}


def stable_json_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def story_hash(suite: dict[str, Any]) -> str:
    payload = {
        "suite_id": suite.get("suite_id"),
        "version": suite.get("version"),
        "prompt_version": suite.get("prompt_version"),
        "runtime_contract": suite.get("runtime_contract", {}),
        "dotnet": suite.get("dotnet", {}),
        "project_story": suite.get("project_story", {}),
        "architecture": suite.get("architecture", {}),
        "data_model": suite.get("data_model", {}),
        "api_contract": suite.get("api_contract", {}),
        "tasks": suite.get("tasks", []),
        "validators": suite.get("validators", []),
    }
    return stable_json_hash(payload)


def fixture_hash() -> str:
    return stable_json_hash(FILES)


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_fixture(project_root: Path) -> list[str]:
    written: list[str] = []
    for relative, content in FILES.items():
        path = project_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(relative)
    return written


def run_command(command: list[str], cwd: Path, timeout_seconds: int = 240) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "command": " ".join(command),
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "stdout_tail": completed.stdout[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": " ".join(command),
            "ok": False,
            "returncode": None,
            "elapsed_seconds": timeout_seconds,
            "stdout_tail": str(exc)[-4000:],
            "timed_out": True,
        }


def static_checks(project_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    expected_files = ["InventoryService.slnx", *FILES.keys()]
    for relative in expected_files:
        checks.append(
            {
                "name": f"{relative} exists",
                "ok": (project_root / relative).exists(),
                "path": relative,
            }
        )
    required_substrings = {
        "src/InventoryService.Api/InventoryService.Api.csproj": [
            "<TargetFramework>net10.0</TargetFramework>",
            "Microsoft.EntityFrameworkCore.Sqlite",
        ],
        "src/InventoryService.Api/Program.cs": [
            "AddDbContext<InventoryDbContext>",
            "MapProductEndpoints",
            "MapReservationEndpoints",
            "MapReportEndpoints",
            "AddProblemDetails",
            "MapHealthChecks",
        ],
        "src/InventoryService.Api/Data/InventoryDbContext.cs": [
            "DbSet<Product>",
            "DbSet<Reservation>",
            "HasIndex(product => product.Sku).IsUnique()",
            "HasIndex(reservation => reservation.IdempotencyKey).IsUnique()",
        ],
        "src/InventoryService.Api/Features/Products/ProductEndpoints.cs": [
            "MapGroup(\"/products\")",
            "ProductService service",
        ],
        "src/InventoryService.Api/Features/Reservations/ReservationEndpoints.cs": [
            "MapGroup(\"/reservations\")",
            "Idempotency-Key",
            "ReservationService service",
        ],
        "src/InventoryService.Api/Features/Reports/ReportEndpoints.cs": [
            "MapGroup(\"/reports\")",
            "ReportService service",
        ],
        "tests/InventoryService.Api.Tests/InventoryApiFactory.cs": [
            "WebApplicationFactory<Program>",
            "DataSource=:memory:",
            "EnsureCreatedAsync",
        ],
        "tests/InventoryService.Api.Tests/InventoryWorkflowTests.cs": [
            "Reservation_is_idempotent",
            "Reservation_rejects_insufficient_inventory",
            "Cancel_and_fulfill_update_low_stock_report",
        ],
    }
    for relative, snippets in required_substrings.items():
        text = common.read_text(project_root / relative)
        for snippet in snippets:
            checks.append(
                {
                    "name": f"{relative} contains {snippet}",
                    "ok": snippet in text,
                    "path": relative,
                }
            )
    program_text = common.read_text(project_root / "src/InventoryService.Api/Program.cs")
    for forbidden in ("EnsureCreated", "Migrate("):
        checks.append(
            {
                "name": f"production Program.cs does not call {forbidden}",
                "ok": forbidden not in program_text,
                "path": "src/InventoryService.Api/Program.cs",
            }
        )
    return checks


def generate_fixture(
    *,
    output_root: Path,
    run_id: str,
    suite_path: Path = DEFAULT_SUITE,
    project_work_root: Path | None = None,
    write: bool = False,
    run_tests: bool = False,
    allow_existing: bool = False,
) -> dict[str, Any]:
    common.require_supported_python()
    suite = common.load_suite(suite_path)
    run_root = output_root.expanduser().resolve() / run_id
    project_parent = (
        project_work_root.expanduser().resolve() / run_id
        if project_work_root is not None
        else run_root
    )
    project_root = project_parent / "project"
    if run_root.exists() and any(run_root.iterdir()) and not allow_existing:
        raise SystemExit(f"fixture folder already exists and is not empty: {run_root}")
    if project_parent != run_root and project_parent.exists() and any(project_parent.iterdir()) and not allow_existing:
        raise SystemExit(f"project work folder already exists and is not empty: {project_parent}")
    commands: list[dict[str, Any]] = []
    if write or run_tests:
        project_root.mkdir(parents=True, exist_ok=True)
        written = write_fixture(project_root)
        commands.append(
            run_command(
                ["dotnet", "new", "sln", "--format", "slnx", "--name", "InventoryService"],
                project_root,
                timeout_seconds=60,
            )
        )
        commands.append(
            run_command(
                [
                    "dotnet",
                    "sln",
                    "InventoryService.slnx",
                    "add",
                    "src/InventoryService.Api/InventoryService.Api.csproj",
                ],
                project_root,
                timeout_seconds=60,
            )
        )
        commands.append(
            run_command(
                [
                    "dotnet",
                    "sln",
                    "InventoryService.slnx",
                    "add",
                    "tests/InventoryService.Api.Tests/InventoryService.Api.Tests.csproj",
                ],
                project_root,
                timeout_seconds=60,
            )
        )
        if (project_root / "InventoryService.slnx").exists():
            written = ["InventoryService.slnx", *written]
    else:
        written = ["InventoryService.slnx", *sorted(FILES)]
    checks = static_checks(project_root) if project_root.exists() else []
    if run_tests:
        commands.append(
            run_command(
                [
                    "dotnet",
                    "test",
                    "tests/InventoryService.Api.Tests/InventoryService.Api.Tests.csproj",
                    "--nologo",
                    "-v",
                    "minimal",
                ],
                project_root,
                timeout_seconds=360,
            )
        )
    actual_story_hash = story_hash(suite)
    actual_fixture_hash = fixture_hash()
    expected_story_hash = str(suite.get("story_hash", "") or "")
    expected_fixture_hash = str(suite.get("reference_fixture", {}).get("fixture_hash", "") or "")
    hash_checks = [
        {
            "name": "story hash matches suite metadata",
            "ok": not expected_story_hash or expected_story_hash == actual_story_hash,
            "expected": expected_story_hash,
            "actual": actual_story_hash,
        },
        {
            "name": "fixture hash matches suite metadata",
            "ok": not expected_fixture_hash or expected_fixture_hash == actual_fixture_hash,
            "expected": expected_fixture_hash,
            "actual": actual_fixture_hash,
        },
    ]
    report = {
        "schema_version": 1,
        "tool": "agent-benchmarking.dotnet-feature-project-fixture",
        "ok": all(item["ok"] for item in checks)
        and all(item["ok"] for item in commands)
        and all(item["ok"] for item in hash_checks),
        "status": "validated" if run_tests else "written" if write else "planned",
        "generated_at": now_utc(),
        "run_id": run_id,
        "suite_id": suite.get("suite_id"),
        "story_hash": actual_story_hash,
        "fixture_hash": actual_fixture_hash,
        "hash_checks": hash_checks,
        "suite_path": str(suite_path),
        "evidence_root": str(run_root),
        "project_root": str(project_root),
        "project_path_length": len(str(project_root)),
        "files": written,
        "feature_slices": suite.get("architecture", {}).get("slices", []),
        "business_rules": suite.get("project_story", {}).get("business_rules", []),
        "static_checks": checks,
        "commands": commands,
        "token_counter": common.token_count_metadata(),
        "advisory_token_estimates": {
            "fixture_source_tokens": sum(common.estimate_tokens(content) for content in FILES.values()),
            "method": common.TOKEN_ESTIMATION_METHOD,
        },
    }
    if write or run_tests:
        common.write_json(run_root / "summary.json", report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--suite", default=str(DEFAULT_SUITE))
    parser.add_argument("--project-work-root", default="", help="optional short root for the generated .NET project")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--run-tests", action="store_true")
    parser.add_argument("--allow-existing", action="store_true")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown", dest="output_format")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = generate_fixture(
        output_root=Path(args.output_root),
        run_id=args.run_id,
        suite_path=Path(args.suite),
        project_work_root=Path(args.project_work_root) if args.project_work_root else None,
        write=bool(args.write or args.run_tests),
        run_tests=bool(args.run_tests),
        allow_existing=bool(args.allow_existing),
    )
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"# .NET Feature Project Fixture\n\n"
            f"- Run: `{report['run_id']}`\n"
            f"- Status: `{report['status']}`\n"
            f"- OK: {str(report['ok']).lower()}\n"
            f"- Project: `{report['project_root']}`\n"
        )
    return 0 if report["ok"] or not args.run_tests else 1


if __name__ == "__main__":
    raise SystemExit(main())
