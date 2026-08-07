import Toybox.Lang;

module RunnerFormatting {
    function elapsed(totalSeconds as Number) as String {
        var minutes = totalSeconds / 60;
        var seconds = totalSeconds % 60;
        return minutes.format("%02d") + ":" + seconds.format("%02d");
    }
}
