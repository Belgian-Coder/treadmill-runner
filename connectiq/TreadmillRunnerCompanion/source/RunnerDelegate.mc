import Toybox.Lang;
import Toybox.WatchUi;

class RunnerDelegate extends WatchUi.BehaviorDelegate {
    private var _controller as RunnerController;

    function initialize(controller as RunnerController) {
        BehaviorDelegate.initialize();
        _controller = controller;
    }

    function onSelect() as Boolean {
        if (_controller.isSavePending()) { return _controller.retrySave(); }
        return _controller.isRecording() ? _controller.stopAndSave() : _controller.startRecording();
    }

    function onBack() as Boolean {
        // Prevent an accidental Back press from ending or discarding a recording.
        return _controller.protectsBack();
    }

    function onNextPage() as Boolean {
        return _controller.showNextPage();
    }

    function onPreviousPage() as Boolean {
        return _controller.showPreviousPage();
    }
}
