import Toybox.Lang;

module RunnerFormatting {
    function elapsed(totalSeconds as Number) as String {
        var minutes = totalSeconds / 60;
        var seconds = totalSeconds % 60;
        return minutes.format("%02d") + ":" + seconds.format("%02d");
    }

    function metric(value as Number?, suffix as String) as String {
        return value == null ? "--" : (value as Number).toString() + suffix;
    }

    function distance(meters as Float?) as String {
        return meters == null ? "--" : ((meters as Float) / 1000.0).format("%.2f") + " km";
    }

    function speed(metersPerSecond as Float?) as String {
        return metersPerSecond == null ? "--" : ((metersPerSecond as Float) * 3.6).format("%.1f") + " km/h";
    }
}
