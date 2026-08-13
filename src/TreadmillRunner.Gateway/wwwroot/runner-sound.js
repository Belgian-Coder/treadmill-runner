if (window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true) {
  document.documentElement.classList.add("standalone-shell");
}

window.treadmillRunnerSound = {
  playCue: function (volumePercent = 60) {
    const AudioContextType = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextType) return false;
    const context = new AudioContextType();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(660, context.currentTime);
    gain.gain.setValueAtTime(0.0001, context.currentTime);
    const volume = Math.max(0, Math.min(100, Number(volumePercent) || 0)) / 100;
    gain.gain.exponentialRampToValueAtTime(Math.max(0.0001, 0.2 * volume), context.currentTime + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.18);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.2);
    oscillator.addEventListener("ended", () => context.close());
    return true;
  }
};

window.treadmillRunnerRuntime = {
  visibilityHandler: null,
  reference: null,
  initialize: function (reference) {
    this.dispose();
    this.reference = reference;
    this.visibilityHandler = () => {
      if (document.visibilityState === "visible") this.reference?.invokeMethodAsync("BrowserVisibleAsync");
    };
    document.addEventListener("visibilitychange", this.visibilityHandler);
  },
  reload: function (fingerprint) {
    const url = new URL(window.location.href);
    if (url.searchParams.get("build") === fingerprint) return false;

    const key = `treadmillrunner.reload.${fingerprint}`;
    try {
      if (sessionStorage.getItem(key) === "attempted") return false;
      sessionStorage.setItem(key, "attempted");
    } catch {
      // Privacy modes can disable session storage. The build query remains a
      // one-attempt guard if a stale entry document is returned after reload.
    }

    url.searchParams.set("build", fingerprint);
    url.hash = "signed-updates";
    window.location.replace(url.toString());
    return true;
  },
  reloadNow: function (fingerprint) {
    const url = new URL(window.location.href);
    url.searchParams.set("build", fingerprint);
    url.searchParams.set("reload", Date.now().toString());
    url.hash = "signed-updates";
    window.location.replace(url.toString());
  },
  dispose: function () {
    if (this.visibilityHandler) document.removeEventListener("visibilitychange", this.visibilityHandler);
    this.visibilityHandler = null;
    this.reference = null;
  }
};

window.treadmillRunnerDrafts = {
  prefix: "treadmillrunner.draft.v1.",
  save: function (key, payload) {
    const text = String(payload ?? "");
    if (text.length === 0 || new TextEncoder().encode(text).length > 262144) return false;
    try {
      localStorage.setItem(this.prefix + key, JSON.stringify({ schemaVersion: 1, savedAtUtc: new Date().toISOString(), payload: text }));
      return true;
    } catch { return false; }
  },
  load: function (key) {
    const storageKey = this.prefix + key;
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw || new TextEncoder().encode(raw).length > 262144) { localStorage.removeItem(storageKey); return null; }
      const draft = JSON.parse(raw);
      const age = Date.now() - Date.parse(draft.savedAtUtc);
      if (draft.schemaVersion !== 1 || typeof draft.payload !== "string" || !Number.isFinite(age) || age < 0 || age > 30 * 86400000) {
        localStorage.removeItem(storageKey);
        return null;
      }
      return draft.payload;
    } catch { localStorage.removeItem(storageKey); return null; }
  },
  remove: function (key) { try { localStorage.removeItem(this.prefix + key); } catch { } }
};

window.treadmillRunnerView = {
  autoHideHeaderCleanup: null,
  modalCleanup: null,
  wakeLock: null,
  wakeLockRequest: null,
  wakeLockGeneration: 0,
  wakeLockWanted: false,
  wakeLockVisibilityHandler: null,
  wakeLockStatusCallback: null,
  lastWakeLockStatusKey: null,
  fullscreenStatusCallback: null,
  fullscreenChangeHandler: null,
  fullscreenElementId: null,
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
  scrollToCurrentHash: function () {
    if (!window.location.hash) return false;
    const id = decodeURIComponent(window.location.hash.slice(1));
    const target = document.getElementById(id);
    if (!target) return false;
    target.scrollIntoView({ block: "start", behavior: "auto" });
    return true;
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
    let focusFrame = window.requestAnimationFrame(() => {
      focusFrame = 0;
      (dialog.querySelector("[autofocus]") || focusable()[0] || dialog).focus({ preventScroll: true });
    });
    this.modalCleanup = () => {
      if (focusFrame) window.cancelAnimationFrame(focusFrame);
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
  initializeFullscreenTracking: function (elementId, dotnetReference) {
    this.disposeFullscreenTracking();
    this.fullscreenElementId = elementId;
    this.fullscreenStatusCallback = dotnetReference;
    this.fullscreenChangeHandler = async () => {
      const element = document.getElementById(this.fullscreenElementId);
      const active = document.fullscreenElement === element || element?.classList.contains("control-page--immersive") === true;
      try {
        await this.fullscreenStatusCallback?.invokeMethodAsync("FullscreenStatusChanged", active);
      } catch {
        this.fullscreenStatusCallback = null;
      }
    };
    document.addEventListener("fullscreenchange", this.fullscreenChangeHandler);
    this.fullscreenChangeHandler();
  },
  disposeFullscreenTracking: function () {
    if (this.fullscreenChangeHandler) document.removeEventListener("fullscreenchange", this.fullscreenChangeHandler);
    this.fullscreenChangeHandler = null;
    this.fullscreenStatusCallback = null;
    this.fullscreenElementId = null;
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
  notifyWakeLockStatus: async function (status) {
    const key = `${status.state}|${status.supported}|${status.active}|${status.secureContext}|${status.reason}`;
    if (key === this.lastWakeLockStatusKey) return;
    this.lastWakeLockStatusKey = key;
    if (!this.wakeLockStatusCallback) return;
    try {
      await this.wakeLockStatusCallback.invokeMethodAsync("WakeLockStatusChanged", status);
    } catch {
      this.wakeLockStatusCallback = null;
    }
  },
  setRunWakeLock: async function (wanted, statusCallback) {
    if (statusCallback) this.wakeLockStatusCallback = statusCallback;
    this.wakeLockWanted = Boolean(wanted);
    if (!this.wakeLockVisibilityHandler) {
      this.wakeLockVisibilityHandler = async () => {
        if (document.visibilityState === "visible" && this.wakeLockWanted) {
          await this.setRunWakeLock(true);
        } else if (document.visibilityState !== "visible" && this.wakeLockWanted) {
          await this.notifyWakeLockStatus({
            state: "PausedInBackground",
            supported: "wakeLock" in navigator,
            active: false,
            secureContext: window.isSecureContext,
            reason: "Stay-awake pauses while the app is in the background."
          });
        }
      };
      document.addEventListener("visibilitychange", this.wakeLockVisibilityHandler);
    }
    if (!this.wakeLockWanted) {
      this.wakeLockGeneration++;
      if (this.wakeLock) await this.wakeLock.release();
      this.wakeLock = null;
      const released = {
        state: "Released",
        supported: "wakeLock" in navigator,
        active: false,
        secureContext: window.isSecureContext,
        reason: "Screen stay-awake is not needed without an active run."
      };
      await this.notifyWakeLockStatus(released);
      return released;
    }
    if (!("wakeLock" in navigator)) {
      const unsupported = {
        state: "Unsupported",
        supported: false,
        active: false,
        secureContext: window.isSecureContext,
        reason: window.isSecureContext
          ? "This browser does not support screen stay-awake."
          : "Screen stay-awake requires a secure browser connection."
      };
      await this.notifyWakeLockStatus(unsupported);
      return unsupported;
    }
    if (document.visibilityState !== "visible") {
      const paused = {
        state: "PausedInBackground",
        supported: true,
        active: false,
        secureContext: window.isSecureContext,
        reason: "Stay-awake pauses while the app is in the background."
      };
      await this.notifyWakeLockStatus(paused);
      return paused;
    }
    if (!this.wakeLock && !this.wakeLockRequest) {
      const generation = ++this.wakeLockGeneration;
      try {
        const request = navigator.wakeLock.request("screen");
        this.wakeLockRequest = request;
        const acquired = await request;
        if (!this.wakeLockWanted || generation !== this.wakeLockGeneration) {
          await acquired.release();
        } else {
          this.wakeLock = acquired;
        }
        acquired.addEventListener("release", async () => {
          if (this.wakeLock === acquired) this.wakeLock = null;
          await this.notifyWakeLockStatus({
            state: "Released",
            supported: true,
            active: false,
            secureContext: window.isSecureContext,
            reason: this.wakeLockWanted
              ? "The browser released screen stay-awake. Keep the dashboard visible and check device power settings."
              : "Screen stay-awake was released."
          });
          if (this.wakeLockWanted && document.visibilityState === "visible") {
            window.setTimeout(() => this.setRunWakeLock(true), 0);
          }
        });
      } catch {
        this.wakeLock = null;
        const denied = {
          state: "Denied",
          supported: true,
          active: false,
          secureContext: window.isSecureContext,
          reason: "The browser did not allow screen stay-awake."
        };
        await this.notifyWakeLockStatus(denied);
        return denied;
      } finally {
        this.wakeLockRequest = null;
      }
    }
    if (this.wakeLockRequest) {
      try { await this.wakeLockRequest; } catch { }
    }
    const active = {
      state: "Active",
      supported: true,
      active: Boolean(this.wakeLock),
      secureContext: window.isSecureContext,
      reason: "Screen stay-awake is active for this run."
    };
    await this.notifyWakeLockStatus(active);
    return active;
  },
  disposeRunWakeLock: async function () {
    this.wakeLockWanted = false;
    this.wakeLockGeneration++;
    if (this.wakeLock) await this.wakeLock.release();
    this.wakeLock = null;
    if (this.wakeLockVisibilityHandler) {
      document.removeEventListener("visibilitychange", this.wakeLockVisibilityHandler);
      this.wakeLockVisibilityHandler = null;
    }
    this.wakeLockStatusCallback = null;
    this.lastWakeLockStatusKey = null;
  },
  copyText: async function (value) {
    const text = String(value || "");
    if (!text) return false;
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch { }
    }
    const input = document.createElement("textarea");
    input.value = text;
    input.setAttribute("readonly", "");
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.appendChild(input);
    input.select();
    let copied = false;
    try { copied = document.execCommand("copy"); } catch { }
    input.remove();
    return copied;
  }
};
