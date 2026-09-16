using TreadmillRunner.Web.Live;

namespace TreadmillRunner.IntegrationTests;

public sealed class ChartAxisScaleTests
{
  [Fact]
  public void Signed_incline_scale_keeps_negative_zero_and_positive_values_distinct()
  {
    ChartAxisScale scale = ChartAxisScale.Create([-2d, 0d, 3d]);

    Assert.InRange(scale.Ticks.Count, 4, 6);
    Assert.Contains(scale.Ticks, static tick => tick.Value < 0);
    Assert.Contains(scale.Ticks, static tick => tick.Value == 0 && tick.Label == "0");
    Assert.Contains(scale.Ticks, static tick => tick.Value > 0);
    Assert.True(scale.ProjectY(-2) > scale.ProjectY(0));
    Assert.True(scale.ProjectY(0) > scale.ProjectY(3));
  }

  [Fact]
  public void Tick_positions_use_the_same_ten_to_two_hundred_ten_projection_as_paths()
  {
    ChartAxisScale scale = ChartAxisScale.CreateFromBounds(0, 10);

    Assert.Equal(5, scale.Ticks.Count);
    Assert.Equal(["10", "7.5", "5", "2.5", "0"], scale.Ticks.Select(static tick => tick.Label));
    Assert.All(scale.Ticks, tick => Assert.Equal(scale.ProjectY(tick.Value), tick.SvgY, 8));
    Assert.Equal(10, scale.ProjectY(scale.Maximum), 8);
    Assert.Equal(210, scale.ProjectY(scale.Minimum), 8);
  }

  [Fact]
  public void Svg_coordinates_are_serialized_with_a_decimal_point_independent_of_current_culture()
  {
    Assert.Equal("42.75", ChartAxisScale.SvgCoordinate(42.75));
  }
}
