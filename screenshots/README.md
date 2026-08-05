# TreadmillRunner screen gallery

This folder is the local Playwright-generated visual gallery for the application. Generated images are intentionally ignored by Git because populated fixtures may resemble household data. Run:

```powershell
./eng/playwright.ps1 -Configuration Release
```

The browser suite refreshes a stable `1180×820` desktop PNG and a `440×956` iPhone 17 Pro Max PNG for every routed application screen. Each root-gallery image uses the same deterministic household scenario: two runners, complete Z1–Z5 heart-rate zones, 5K and 10K workout sets, a populated calendar, realistic session history, an active interval graph, and actionable update status. It also keeps portrait (`440×956`) and landscape (`956×440`) iPhone 17 Pro Max images for the real-time dashboard alongside tablet and desktop variants. Portrait keeps one vertical preset column per side; landscape uses two 44px-or-larger preset buttons across per side. Both phone orientations include a true browser Fullscreen API capture and the immersive fallback. `control-chart-focused-iphone17-pro-max.png` proves the one-tap expanded portrait graph keeps Stop visible. `navigation-hidden-iphone17-pro-max.png` covers the compact auto-hiding shell. `control-live-heart-rate-iphone17-pro-max.png` is generated after live speed, incline, and heart-rate automation changes; its elapsed-time cursor and latest measured marker are asserted to be inside the graph rather than at the starting edge. Axis assertions prove 10 km/h and 10% default ceilings plus immediate independent expansion when live data exceeds either default. Feature-specific acceptance screenshots remain under `validation/playwright/accepted/` when additional transient states are required.

Gallery data is test-only. Planning data is written to the browser fixture's disposable SQLite database through the normal local APIs, and the active dashboard uses Development simulator endpoints. Device status, historical totals, session detail, and available-update status are fulfilled only inside the Playwright page so gallery generation never scans or connects Bluetooth, sends treadmill commands, stages a package, activates an update, or touches the installed Windows service.

Do not edit the PNG files manually; update the page or browser fixture and rerun Playwright. A fresh public clone contains this README and creates the gallery on its first browser run.
