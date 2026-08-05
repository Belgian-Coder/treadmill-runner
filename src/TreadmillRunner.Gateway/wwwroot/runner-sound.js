window.treadmillRunnerSound = {
  playCue: function () {
    const AudioContextType = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextType) return false;
    const context = new AudioContextType();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(660, context.currentTime);
    gain.gain.setValueAtTime(0.0001, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.12, context.currentTime + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.18);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.2);
    oscillator.addEventListener("ended", () => context.close());
    return true;
  }
};

window.treadmillRunnerView = {
  autoHideHeaderCleanup: null,
  modalCleanup: null,
  wakeLock: null,
  wakeLockWanted: false,
  wakeLockVisibilityHandler: null,
  initializeAutoHideHeader: function (elementId) {
    this.disposeAutoHideHeader();
    const header = document.getElementById(elementId);
    if (!header) return;
    let previousY = window.scrollY;
    let scheduled = false;
    const show = () => header.dataset.scrollState = "shown";
    const update = () => {
      scheduled = false;
      const currentY = window.scrollY;
      const delta = currentY - previousY;
      const menuOpen = Boolean(header.querySelector(".nav-more[open]"));
      const hasFocus = header.contains(document.activeElement);
      if (currentY < 24 || delta < -12 || menuOpen || hasFocus) show();
      else if (currentY > 80 && delta >= 0) header.dataset.scrollState = "hidden";
      previousY = currentY;
    };
    const onScroll = () => {
      if (scheduled) return;
      scheduled = true;
      window.requestAnimationFrame(update);
    };
    const onInteraction = () => show();
    window.addEventListener("scroll", onScroll, { passive: true });
    header.addEventListener("focusin", onInteraction);
    header.addEventListener("pointerenter", onInteraction);
    header.addEventListener("toggle", onInteraction, true);
    this.autoHideHeaderCleanup = () => {
      window.removeEventListener("scroll", onScroll);
      header.removeEventListener("focusin", onInteraction);
      header.removeEventListener("pointerenter", onInteraction);
      header.removeEventListener("toggle", onInteraction, true);
    };
  },
  disposeAutoHideHeader: function () {
    if (this.autoHideHeaderCleanup) this.autoHideHeaderCleanup();
    this.autoHideHeaderCleanup = null;
  },
  openModal: function (backdropId, dotnetReference) {
    this.closeModal();
    const backdrop = document.getElementById(backdropId);
    const dialog = backdrop?.querySelector('[role="dialog"]');
    if (!backdrop || !dialog) return;
    const previousFocus = document.activeElement;
    const previousDocumentOverflow = document.documentElement.style.overflow;
    const previousBodyOverflow = document.body.style.overflow;
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";
    const header = document.getElementById("site-header");
    if (header) header.inert = true;
    const background = Array.from(document.getElementById("main-content")?.children || [])
      .filter(element => element !== backdrop);
    background.forEach(element => element.inert = true);
    const focusableSelector = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';
    const focusable = () => Array.from(dialog.querySelectorAll(focusableSelector));
    const onKeyDown = async event => {
      if (event.key === "Escape") {
        event.preventDefault();
        const closed = await dotnetReference.invokeMethodAsync("CloseScheduleManagerFromJs");
        if (closed) window.treadmillRunnerView.closeModal();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    (dialog.querySelector("[autofocus]") || focusable()[0] || dialog).focus();
    this.modalCleanup = () => {
      document.removeEventListener("keydown", onKeyDown);
      background.forEach(element => element.inert = false);
      if (header) header.inert = false;
      document.documentElement.style.overflow = previousDocumentOverflow;
      document.body.style.overflow = previousBodyOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
      dotnetReference.dispose();
    };
  },
  focusModalStage: function (elementId) {
    const stage = document.getElementById(elementId);
    if (stage) stage.focus({ preventScroll: true });
  },
  closeModal: function () {
    if (this.modalCleanup) this.modalCleanup();
    this.modalCleanup = null;
  },
  toggleFullscreen: async function (elementId) {
    if (document.fullscreenElement) {
      await document.exitFullscreen();
      return "exited";
    }

    const element = document.getElementById(elementId);
    if (!element) return "unavailable";
    if (element.classList.contains("control-page--immersive")) {
      element.classList.remove("control-page--immersive");
      document.body.classList.remove("immersive-view");
      return "exited";
    }
    if (element.requestFullscreen) {
      try {
        await element.requestFullscreen();
        return "native";
      } catch { }
    }
    element.classList.add("control-page--immersive");
    document.body.classList.add("immersive-view");
    return "fallback";
  },
  clearImmersiveView: function (elementId) {
    document.getElementById(elementId)?.classList.remove("control-page--immersive");
    document.body.classList.remove("immersive-view");
    return "cleared";
  },
  setRunWakeLock: async function (wanted) {
    this.wakeLockWanted = Boolean(wanted);
    if (!this.wakeLockVisibilityHandler) {
      this.wakeLockVisibilityHandler = async () => {
        if (document.visibilityState === "visible" && this.wakeLockWanted) {
          await this.setRunWakeLock(true);
        }
      };
      document.addEventListener("visibilitychange", this.wakeLockVisibilityHandler);
    }
    if (!this.wakeLockWanted) {
      if (this.wakeLock) await this.wakeLock.release();
      this.wakeLock = null;
      return { supported: "wakeLock" in navigator, active: false };
    }
    if (!("wakeLock" in navigator) || document.visibilityState !== "visible") {
      return { supported: "wakeLock" in navigator, active: false };
    }
    if (!this.wakeLock) {
      try {
        this.wakeLock = await navigator.wakeLock.request("screen");
        this.wakeLock.addEventListener("release", () => { this.wakeLock = null; });
      } catch {
        this.wakeLock = null;
      }
    }
    return { supported: true, active: Boolean(this.wakeLock) };
  },
  disposeRunWakeLock: async function () {
    this.wakeLockWanted = false;
    if (this.wakeLock) await this.wakeLock.release();
    this.wakeLock = null;
    if (this.wakeLockVisibilityHandler) {
      document.removeEventListener("visibilitychange", this.wakeLockVisibilityHandler);
      this.wakeLockVisibilityHandler = null;
    }
  }
};
