import Toybox.Graphics;
import Toybox.Lang;
import Toybox.Timer;
import Toybox.WatchUi;

class RunnerView extends WatchUi.View {
    private var _controller as RunnerController;
    private var _timer as Timer.Timer?;
    private var _ticks as Number = 0;

    function initialize(controller as RunnerController) {
        View.initialize();
        _controller = controller;
    }

    function onShow() as Void {
        _controller.refreshGatewayStatus();
        _timer = new Timer.Timer();
        (_timer as Timer.Timer).start(method(:tick), 1000, true);
    }

    function onHide() as Void {
        if (_timer != null) { (_timer as Timer.Timer).stop(); }
        _timer = null;
    }

    function tick() as Void {
        _ticks += 1;
        if ((_ticks % 30) == 0) { _controller.refreshGatewayStatus(); }
        WatchUi.requestUpdate();
    }

    function onUpdate(dc as Dc) as Void {
        var width = dc.getWidth();
        var height = dc.getHeight();
        dc.setColor(Graphics.COLOR_BLACK, Graphics.COLOR_BLACK);
        dc.clear();
        dc.setColor(0x74E0BD, Graphics.COLOR_TRANSPARENT);
        dc.drawText(width / 2, height * 0.16, Graphics.FONT_MEDIUM, _controller.runnerName(), Graphics.TEXT_JUSTIFY_CENTER);
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(width / 2, height * 0.33, Graphics.FONT_SMALL, _controller.sessionTitle(), Graphics.TEXT_JUSTIFY_CENTER);

        if (_controller.isSavePending()) {
            dc.setColor(0xF58B8B, Graphics.COLOR_TRANSPARENT);
            dc.drawText(width / 2, height * 0.50, Graphics.FONT_MEDIUM, "SAVE FAILED", Graphics.TEXT_JUSTIFY_CENTER);
            dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
            dc.drawText(width / 2, height * 0.72, Graphics.FONT_SMALL, "Select: retry save", Graphics.TEXT_JUSTIFY_CENTER);
        } else if (_controller.isRecording()) {
            dc.setColor(0xF5C768, Graphics.COLOR_TRANSPARENT);
            dc.drawText(width / 2, height * 0.50, Graphics.FONT_NUMBER_MEDIUM, formatTime(_controller.elapsedSeconds()), Graphics.TEXT_JUSTIFY_CENTER);
            dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
            dc.drawText(width / 2, height * 0.72, Graphics.FONT_SMALL, "Select: stop + save", Graphics.TEXT_JUSTIFY_CENTER);
        } else {
            dc.setColor(0x74E0BD, Graphics.COLOR_TRANSPARENT);
            dc.drawText(width / 2, height * 0.50, Graphics.FONT_LARGE, "READY", Graphics.TEXT_JUSTIFY_CENTER);
            dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
            dc.drawText(width / 2, height * 0.72, Graphics.FONT_SMALL, "Select: record treadmill", Graphics.TEXT_JUSTIFY_CENTER);
        }
        dc.setColor(0x91ABAA, Graphics.COLOR_TRANSPARENT);
        dc.drawText(width / 2, height * 0.84, Graphics.FONT_XTINY, _controller.gatewayState(), Graphics.TEXT_JUSTIFY_CENTER);
    }

    private function formatTime(total as Number) as String {
        var minutes = total / 60;
        var seconds = total % 60;
        return minutes.format("%02d") + ":" + seconds.format("%02d");
    }
}
