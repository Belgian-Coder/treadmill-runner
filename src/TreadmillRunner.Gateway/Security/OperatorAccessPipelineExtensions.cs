namespace TreadmillRunner.Gateway.Security;

public static class OperatorAccessPipelineExtensions
{
  public static IApplicationBuilder UseOptionalOperatorAccess(this IApplicationBuilder app) =>
    app.Use(async (context, next) =>
    {
      OperatorAccessService access = context.RequestServices.GetRequiredService<OperatorAccessService>();
      bool mutation = !HttpMethods.IsGet(context.Request.Method) &&
        !HttpMethods.IsHead(context.Request.Method) &&
        !HttpMethods.IsOptions(context.Request.Method);
      bool protectedMutation = mutation &&
        context.Request.Path.StartsWithSegments("/api", StringComparison.OrdinalIgnoreCase) &&
        !context.Request.Path.Equals("/api/operator/login", StringComparison.OrdinalIgnoreCase);

      if (access.Enabled && protectedMutation &&
          !access.IsAuthenticated(context.Request.Headers.Authorization.ToString(), out _))
      {
        context.Response.StatusCode = StatusCodes.Status401Unauthorized;
        context.Response.Headers.WWWAuthenticate = "Bearer";
        await context.Response.WriteAsJsonAsync(new
        {
          type = "https://treadmillrunner.local/problems/operator-access-required",
          title = "Operator access required",
          status = StatusCodes.Status401Unauthorized,
          code = "OperatorAccessRequired",
          detail = "Unlock operator controls before changing state.",
        });
        return;
      }
      await next();
    });
}
