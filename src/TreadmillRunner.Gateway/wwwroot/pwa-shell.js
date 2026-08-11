(function () {
  "use strict";

  let installPrompt = null;
  let statusReference = null;
  let serviceWorkerRegistered = false;
  let serviceWorkerMessage = "Offline safety is unavailable in this browser.";

  const pwaOriginEligible = () => {
    const host = window.location.hostname.toLowerCase();
    const loopback = host === "localhost" || host.endsWith(".localhost") ||
      host === "::1" || host === "[::1]" || /^127(?:\.\d{1,3}){3}$/.test(host);
    return window.isSecureContext && (window.location.protocol === "https:" || loopback);
  };

  const isStandalone = () =>
    window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

  const currentStatus = () => ({
    secureContext: pwaOriginEligible(),
    standalone: isStandalone(),
    installPromptAvailable: installPrompt !== null,
    serviceWorkerSupported: "serviceWorker" in navigator,
    serviceWorkerRegistered,
    shareSupported: typeof navigator.share === "function" &&
      typeof navigator.canShare === "function" &&
      typeof window.File === "function",
    serviceWorkerMessage
  });

  const notifyStatus = async () => {
    if (!statusReference) return;
    try {
      await statusReference.invokeMethodAsync("PwaStatusChanged", currentStatus());
    } catch {
      statusReference = null;
    }
  };

  const registrationPromise = (async () => {
    if (!("serviceWorker" in navigator)) return;
    if (!pwaOriginEligible()) {
      serviceWorkerMessage = "Offline safety requires the trusted HTTPS address.";
      return;
    }

    try {
      await navigator.serviceWorker.register("/service-worker.js", {
        scope: "/",
        updateViaCache: "none"
      });
      serviceWorkerRegistered = true;
      serviceWorkerMessage = "The offline safety page is ready. TreadmillRunner itself still requires the gateway.";
    } catch {
      serviceWorkerMessage = "The browser could not prepare the offline safety page.";
    } finally {
      await notifyStatus();
    }
  })();

  window.addEventListener("beforeinstallprompt", event => {
    event.preventDefault();
    installPrompt = event;
    void notifyStatus();
  });

  window.addEventListener("appinstalled", () => {
    installPrompt = null;
    void notifyStatus();
  });

  const displayMode = window.matchMedia("(display-mode: standalone)");
  displayMode.addEventListener?.("change", () => void notifyStatus());

  const safeFileName = (value, fallback) => {
    const candidate = String(value || "")
      .replace(/[\u0000-\u001f\u007f<>:"/\\|?*]+/g, "-")
      .replace(/[. ]+$/g, "")
      .trim();
    return candidate || fallback;
  };

  const responseFileName = (response, fallback) => {
    const disposition = response.headers.get("content-disposition") || "";
    const encoded = disposition.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
    if (encoded) {
      try { return safeFileName(decodeURIComponent(encoded[1]), fallback); } catch { }
    }
    const quoted = disposition.match(/filename\s*=\s*"([^"]+)"/i);
    if (quoted) return safeFileName(quoted[1], fallback);
    const plain = disposition.match(/filename\s*=\s*([^;]+)/i);
    return safeFileName(plain?.[1], fallback);
  };

  const downloadBlob = (blob, fileName) => {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = fileName;
    anchor.style.display = "none";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  window.treadmillRunnerPwa = {
    initialize: async function (reference) {
      statusReference = reference || null;
      await registrationPromise;
      return currentStatus();
    },

    getStatus: function () {
      return currentStatus();
    },

    promptInstall: async function () {
      if (isStandalone()) {
        return { state: "Installed", message: "TreadmillRunner is already open as an installed app." };
      }
      if (!installPrompt) {
        return { state: "Unavailable", message: "Use this browser's install command, or Safari Share → Add to Home Screen." };
      }

      try {
        const prompt = installPrompt;
        installPrompt = null;
        await prompt.prompt();
        const choice = await prompt.userChoice;
        await notifyStatus();
        return choice?.outcome === "accepted"
          ? { state: "Accepted", message: "Installation was accepted. Open TreadmillRunner from the new app icon." }
          : { state: "Dismissed", message: "Installation was dismissed. You can install later from the browser menu." };
      } catch {
        await notifyStatus();
        return { state: "Failed", message: "The browser could not open its installation prompt." };
      }
    },

    shareOrDownload: async function (path, fallbackFileName, title) {
      let target;
      try {
        target = new URL(path, window.location.href);
        if (target.origin !== window.location.origin) throw new Error("cross-origin");
      } catch {
        return { state: "Failed", message: "Only files from this local TreadmillRunner gateway can be shared." };
      }

      try {
        const response = await fetch(target, {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          headers: { "Accept": "*/*" }
        });
        if (!response.ok) {
          return {
            state: "Failed",
            message: response.status === 409
              ? "The file is available only while no workout is active. Nothing was downloaded or shared."
              : `The gateway could not prepare the file (HTTP ${response.status}).`
          };
        }

        const blob = await response.blob();
        const fileName = responseFileName(response, safeFileName(fallbackFileName, "treadmillrunner-export"));
        const type = (response.headers.get("content-type") || blob.type || "application/octet-stream").split(";")[0];
        const file = new File([blob], fileName, { type, lastModified: Date.now() });
        const shareData = { files: [file], title: String(title || "TreadmillRunner export") };

        let canShareFiles = false;
        if (typeof navigator.share === "function" && typeof navigator.canShare === "function") {
          try { canShareFiles = navigator.canShare(shareData); } catch { }
        }
        if (canShareFiles) {
          try {
            await navigator.share(shareData);
            return { state: "Shared", message: `${fileName} was sent to the device share sheet.` };
          } catch (error) {
            if (error?.name === "AbortError") return { state: "Canceled", message: "Sharing was canceled." };
            return { state: "Failed", message: "The device share sheet could not share this file. Use Download instead." };
          }
        }

        downloadBlob(blob, fileName);
        return { state: "Downloaded", message: "This browser cannot share that file type, so it was downloaded instead." };
      } catch {
        return { state: "Failed", message: "The local gateway became unavailable before the file could be shared." };
      }
    },

    dispose: function () {
      statusReference = null;
    }
  };
})();
