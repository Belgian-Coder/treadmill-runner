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

        if (_controller.isRecording() && _controller.isMetricsPage()) {
            drawMetrics(dc, width, height);
            return;
        }

        dc.setColor(0x74E0BD, Graphics.COLOR_TRANSPARENT);
        dc.drawText(width / 2, height * 0.16, Graphics.FONT_MEDIUM, _controller.runnerName(), Graphics.TEXT_JUSTIFY_CENTER);
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(width / 2, height * 0.27, Graphics.FONT_SMALL, _controller.sessionTitle(), Graphics.TEXT_JUSTIFY_CENTER);

        if (_controller.isSavePending()) {
            dc.setColor(0xF58B8B, Graphics.COLOR_TRANSPARENT);
            dc.drawText(width / 2, height * 0.50, Graphics.FONT_MEDIUM, "SAVE FAILED", Graphics.TEXT_JUSTIFY_CENTER);
            dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
            dc.drawText(width / 2, height * 0.72, Graphics.FONT_SMALL, "Select: retry", Graphics.TEXT_JUSTIFY_CENTER);
        } else if (_controller.isRecording()) {
            dc.setColor(0xF5C768, Graphics.COLOR_TRANSPARENT);
            dc.drawText(
                width / 2,
                height * 0.58,
                Graphics.FONT_NUMBER_MEDIUM,
                RunnerFormatting.elapsed(_controller.elapsedSeconds()),
                Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
            dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
            dc.drawText(width / 2, height * 0.72, Graphics.FONT_SMALL, "Select: stop/save", Graphics.TEXT_JUSTIFY_CENTER);
            dc.setColor(0x91ABAA, Graphics.COLOR_TRANSPARENT);
            dc.drawText(width / 2, height * 0.80, Graphics.FONT_XTINY, "↓ metrics · 1/2", Graphics.TEXT_JUSTIFY_CENTER);
        } else {
            dc.setColor(0x74E0BD, Graphics.COLOR_TRANSPARENT);
            dc.drawText(width / 2, height * 0.50, Graphics.FONT_LARGE, "READY", Graphics.TEXT_JUSTIFY_CENTER);
            dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
            dc.drawText(width / 2, height * 0.72, Graphics.FONT_SMALL, "Select: start", Graphics.TEXT_JUSTIFY_CENTER);
        }
        dc.setColor(0x91ABAA, Graphics.COLOR_TRANSPARENT);
        dc.drawText(width / 2, height * 0.87, Graphics.FONT_XTINY, _controller.gatewayState(), Graphics.TEXT_JUSTIFY_CENTER);
    }

    private function drawMetrics(dc as Dc, width as Number, height as Number) as Void {
        dc.setColor(0x74E0BD, Graphics.COLOR_TRANSPARENT);
        dc.drawText(width / 2, height * 0.13, Graphics.FONT_MEDIUM, _controller.runnerName(), Graphics.TEXT_JUSTIFY_CENTER);
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(width / 2, height * 0.23, Graphics.FONT_XTINY, "LIVE METRICS", Graphics.TEXT_JUSTIFY_CENTER);

        drawMetric(dc, (width * 0.28).toNumber(), (height * 0.37).toNumber(), "HEART RATE", RunnerFormatting.metric(_controller.currentHeartRate(), " bpm"), true);
        drawMetric(dc, (width * 0.72).toNumber(), (height * 0.37).toNumber(), "CALORIES", RunnerFormatting.metric(_controller.calories(), " kcal"), true);
        drawMetric(dc, (width * 0.28).toNumber(), (height * 0.61).toNumber(), "DISTANCE", RunnerFormatting.distance(_controller.elapsedDistance()), false);
        drawMetric(dc, (width * 0.72).toNumber(), (height * 0.61).toNumber(), "SPEED", RunnerFormatting.speed(_controller.currentSpeed()), false);

        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(width / 2, height * 0.78, Graphics.FONT_SMALL, "Select: stop/save", Graphics.TEXT_JUSTIFY_CENTER);
        dc.setColor(0x91ABAA, Graphics.COLOR_TRANSPARENT);
        dc.drawText(width / 2, height * 0.86, Graphics.FONT_XTINY, "↑ timer · 2/2", Graphics.TEXT_JUSTIFY_CENTER);
    }

    private function drawMetric(dc as Dc, x as Number, y as Number, label as String, value as String, large as Boolean) as Void {
        dc.setColor(0x91ABAA, Graphics.COLOR_TRANSPARENT);
        dc.drawText(x, y, Graphics.FONT_XTINY, label, Graphics.TEXT_JUSTIFY_CENTER);
        dc.setColor(large ? 0xF5C768 : Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(x, y + Graphics.getFontHeight(Graphics.FONT_XTINY), large ? Graphics.FONT_MEDIUM : Graphics.FONT_SMALL, value, Graphics.TEXT_JUSTIFY_CENTER);
    }

}
