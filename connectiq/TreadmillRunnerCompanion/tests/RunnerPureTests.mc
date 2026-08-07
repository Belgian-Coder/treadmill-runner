import Toybox.Lang;
import Toybox.Test;

(:test)
function acceptsCompleteHttpsGatewaySettings(logger as Test.Logger) as Boolean {
    var result = GatewaySettings.isConfigured(
        "https://treadmill.example.test",
        "12345678901234567890"
    );
    logger.debug("complete HTTPS settings accepted=" + result);
    return result;
}

(:test)
function rejectsUnsafeOrIncompleteGatewaySettings(logger as Test.Logger) as Boolean {
    var rejectsHttp = !GatewaySettings.isConfigured(
        "http://treadmill.example.test",
        "12345678901234567890"
    );
    var rejectsShortToken = !GatewaySettings.isConfigured(
        "https://treadmill.example.test",
        "too-short"
    );
    var rejectsMissingValues = !GatewaySettings.isConfigured(null, null);
    logger.debug("invalid settings rejected");
    return rejectsHttp && rejectsShortToken && rejectsMissingValues;
}

(:test)
function formatsElapsedTimeDeterministically(logger as Test.Logger) as Boolean {
    var zero = RunnerFormatting.elapsed(0);
    var minute = RunnerFormatting.elapsed(65);
    var hour = RunnerFormatting.elapsed(3661);
    logger.debug("elapsed examples=" + zero + "," + minute + "," + hour);
    return zero.equals("00:00") && minute.equals("01:05") && hour.equals("61:01");
}

(:test)
function formatsNativeMetricsWithoutInventingValues(logger as Test.Logger) as Boolean {
    var unavailable = RunnerFormatting.metric(null, " bpm");
    var heartRate = RunnerFormatting.metric(142, " bpm");
    var distance = RunnerFormatting.distance(3210.0);
    var speed = RunnerFormatting.speed(3.0);
    var calories = RunnerFormatting.metric(286, " kcal");
    logger.debug("native metric examples=" + heartRate + "," + distance + "," + speed + "," + calories);
    return unavailable.equals("--")
        && heartRate.equals("142 bpm")
        && distance.equals("3.21 km")
        && speed.equals("10.8 km/h")
        && calories.equals("286 kcal");
}
