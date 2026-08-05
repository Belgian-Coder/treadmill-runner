import Toybox.Activity;
import Toybox.ActivityRecording;
import Toybox.Application;
import Toybox.Communications;
import Toybox.Lang;
import Toybox.Sensor;
import Toybox.System;
import Toybox.WatchUi;

class RunnerController {
    private var _session as ActivityRecording.Session?;
    private var _startedAt as Number?;
    private var _savePending as Boolean = false;
    private var _gatewayState as String = "Standalone";
    private var _sessionTitle as String = "Manual treadmill";
    private var _requestPending as Boolean = false;

    function isRecording() as Boolean {
        return _session != null && (_session as ActivityRecording.Session).isRecording();
    }

    function isSavePending() as Boolean { return _savePending; }
    function protectsBack() as Boolean { return _session != null; }

    function runnerName() as String {
        var value = Application.Properties.getValue("runnerName");
        return value instanceof String && (value as String).length() > 0 ? value as String : "Runner";
    }

    function gatewayState() as String { return _gatewayState; }
    function sessionTitle() as String { return _sessionTitle; }

    function elapsedSeconds() as Number {
        return _startedAt == null ? 0 : (System.getTimer() - (_startedAt as Number)) / 1000;
    }

    function startRecording() as Boolean {
        if (_session != null) { return false; }
        Sensor.setEnabledSensors([Sensor.SENSOR_HEARTRATE, Sensor.SENSOR_ONBOARD_HEARTRATE]);
        _session = ActivityRecording.createSession({
            :name => "TreadmillRunner",
            :sport => Activity.SPORT_RUNNING,
            :subSport => Activity.SUB_SPORT_TREADMILL
        });
        if (_session == null || !(_session as ActivityRecording.Session).start()) {
            _session = null;
            Sensor.setEnabledSensors([]);
            return false;
        }
        _savePending = false;
        _startedAt = System.getTimer();
        WatchUi.requestUpdate();
        return true;
    }

    function stopAndSave() as Boolean {
        if (!isRecording()) { return false; }
        var active = _session as ActivityRecording.Session;
        if (!active.stop()) {
            _gatewayState = "Stop failed";
            WatchUi.requestUpdate();
            return false;
        }
        Sensor.setEnabledSensors([]);
        _savePending = true;
        _startedAt = null;
        return retrySave();
    }

    function retrySave() as Boolean {
        if (_session == null || !_savePending) { return false; }
        var saved = (_session as ActivityRecording.Session).save();
        if (saved) {
            _session = null;
            _savePending = false;
            _gatewayState = "Saved";
        } else {
            _gatewayState = "Save failed";
        }
        WatchUi.requestUpdate();
        return saved;
    }

    function onAppStop() as Void {
        // Garmin continues an active recording if the view is backgrounded.
        // Never silently save or discard a runner's activity here.
    }

    function refreshGatewayStatus() as Void {
        if (_requestPending) { return; }
        var baseUrl = Application.Properties.getValue("gatewayUrl");
        var token = Application.Properties.getValue("watchToken");
        if (!(baseUrl instanceof String) || !(token instanceof String) ||
            !(baseUrl as String).startsWith("https://") || (token as String).length() < 20) {
            _gatewayState = "Standalone";
            return;
        }
        _requestPending = true;
        Communications.makeWebRequest(
            (baseUrl as String) + "/api/watch/status",
            {},
            {
                :method => Communications.HTTP_REQUEST_METHOD_GET,
                :headers => { "Authorization" => "Bearer " + (token as String) },
                :responseType => Communications.HTTP_RESPONSE_CONTENT_TYPE_JSON
            },
            method(:onGatewayResponse));
    }

    function onGatewayResponse(code as Number, data as Dictionary or String or Null) as Void {
        _requestPending = false;
        if (code == 200 && data instanceof Dictionary) {
            var payload = data as Dictionary;
            var runner = payload["runnerName"];
            var title = payload["sessionTitle"];
            var state = payload["state"];
            if (runner instanceof String) { Application.Properties.setValue("runnerName", runner); }
            if (title instanceof String) { _sessionTitle = title; }
            _gatewayState = state instanceof String ? state as String : "Connected";
        } else {
            _gatewayState = code == 0 ? "Phone offline" : "Gateway unavailable";
        }
        WatchUi.requestUpdate();
    }
}
