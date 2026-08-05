import Toybox.Application;
import Toybox.Lang;

class TreadmillRunnerApp extends Application.AppBase {
    private var _controller as RunnerController?;

    function initialize() {
        AppBase.initialize();
    }

    function onStart(state as Dictionary?) as Void {
        _controller = new RunnerController();
    }

    function getInitialView() as Array<Views or InputDelegates> {
        if (_controller == null) {
            _controller = new RunnerController();
        }
        return [new RunnerView(_controller), new RunnerDelegate(_controller)];
    }

    function onStop(state as Dictionary?) as Void {
        if (_controller != null) {
            _controller.onAppStop();
        }
    }
}

function getApp() as TreadmillRunnerApp {
    return Application.getApp() as TreadmillRunnerApp;
}
