const states = new WeakMap();

export function initialize(root) {
  if (!root) return;

  let state = states.get(root);
  if (!state) {
    state = createState(root);
    states.set(root, state);
  }

  state.surface = root.querySelector('.chart-inspector__surface');
  state.crosshair = root.querySelector('[data-chart-crosshair]');
  state.tooltip = root.querySelector('[data-chart-tooltip]');
  state.time = root.querySelector('[data-chart-time]');
  state.announcement = root.querySelector('[data-chart-announcement]');
  state.values = [...root.querySelectorAll('[data-chart-value]')];
  state.points = [...root.querySelectorAll('[data-chart-point]')]
    .map(point => ({
      x: Number.parseFloat(point.dataset.x || '0'),
      elapsed: Number.parseFloat(point.dataset.elapsed || '0'),
      values: (point.dataset.values || '').split('|'),
    }))
    .filter(point => Number.isFinite(point.x) && Number.isFinite(point.elapsed))
    .sort((left, right) => left.x - right.x || left.elapsed - right.elapsed);

  if (!state.surface || !state.crosshair || !state.tooltip || state.points.length === 0) {
    hide(state);
    return;
  }

  attach(state);
  if (state.selectedElapsed !== null && (state.visible || state.pinned)) {
    render(state, nearestByElapsed(state.points, state.selectedElapsed), false);
  }
}

export function observeVisibility(root, dotNet) {
  if (!root) return;
  let state = states.get(root);
  if (!state) {
    state = createState(root);
    states.set(root, state);
  }
  state.dotNet = dotNet;
  if (state.observer) return;
  if (!('IntersectionObserver' in window)) {
    dotNet.invokeMethodAsync('SetChartVisibility', true);
    return;
  }
  state.observer = new IntersectionObserver(entries => {
    const visible = entries.some(entry => entry.isIntersecting && entry.intersectionRatio > 0);
    if (visible === state.intersecting) return;
    state.intersecting = visible;
    state.dotNet?.invokeMethodAsync('SetChartVisibility', visible);
  }, { rootMargin: '160px 0px', threshold: 0 });
  state.observer.observe(root);
}

export function dispose(root) {
  const state = states.get(root);
  if (!state) return;
  detach(state);
  state.observer?.disconnect();
  state.observer = null;
  state.dotNet = null;
  states.delete(root);
}

function createState(root) {
  return {
    root,
    surface: null,
    crosshair: null,
    tooltip: null,
    time: null,
    announcement: null,
    values: [],
    points: [],
    selectedElapsed: null,
    visible: false,
    pinned: false,
    attachedSurface: null,
    handlers: null,
    observer: null,
    dotNet: null,
    intersecting: false,
  };
}

function attach(state) {
  if (state.attachedSurface === state.surface) return;
  detach(state);

  const surface = state.surface;
  const handlers = {
    pointerMove(event) {
      if (event.pointerType === 'mouse' || surface.hasPointerCapture?.(event.pointerId)) {
        selectFromPointer(state, event, event.pointerType !== 'mouse');
      }
    },
    pointerDown(event) {
      surface.focus({ preventScroll: true });
      if (event.pointerType === 'touch' || event.pointerType === 'pen') {
        event.preventDefault();
        state.pinned = true;
        surface.setPointerCapture?.(event.pointerId);
      }
      selectFromPointer(state, event, event.pointerType !== 'mouse');
    },
    pointerUp(event) {
      if (surface.hasPointerCapture?.(event.pointerId)) surface.releasePointerCapture(event.pointerId);
    },
    pointerLeave() {
      if (!state.pinned && document.activeElement !== surface) hide(state);
    },
    focus() {
      if (!state.visible && state.points.length > 0) render(state, state.points[state.points.length - 1], true);
    },
    blur(event) {
      if (!state.root.contains(event.relatedTarget)) {
        state.pinned = false;
        hide(state);
      }
    },
    keyDown(event) {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Escape'].includes(event.key)) return;
      event.preventDefault();
      if (event.key === 'Escape') {
        state.pinned = false;
        hide(state);
        return;
      }

      const current = state.selectedElapsed === null
        ? state.points.length - 1
        : nearestElapsedIndex(state.points, state.selectedElapsed);
      const next = event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? state.points.length - 1
          : event.key === 'ArrowLeft'
            ? Math.max(0, current - 1)
            : Math.min(state.points.length - 1, current + 1);
      state.pinned = true;
      render(state, state.points[next], true);
    },
    outsidePointerDown(event) {
      if (state.pinned && !state.root.contains(event.target)) {
        state.pinned = false;
        hide(state);
      }
    },
    resize() {
      if (state.visible && state.selectedElapsed !== null) {
        render(state, nearestByElapsed(state.points, state.selectedElapsed), false);
      }
    },
  };

  surface.addEventListener('pointermove', handlers.pointerMove);
  surface.addEventListener('pointerdown', handlers.pointerDown);
  surface.addEventListener('pointerup', handlers.pointerUp);
  surface.addEventListener('pointercancel', handlers.pointerUp);
  surface.addEventListener('pointerleave', handlers.pointerLeave);
  surface.addEventListener('focus', handlers.focus);
  surface.addEventListener('blur', handlers.blur);
  surface.addEventListener('keydown', handlers.keyDown);
  document.addEventListener('pointerdown', handlers.outsidePointerDown, true);
  window.addEventListener('resize', handlers.resize);
  state.attachedSurface = surface;
  state.handlers = handlers;
}

function detach(state) {
  const surface = state.attachedSurface;
  const handlers = state.handlers;
  if (!surface || !handlers) return;
  surface.removeEventListener('pointermove', handlers.pointerMove);
  surface.removeEventListener('pointerdown', handlers.pointerDown);
  surface.removeEventListener('pointerup', handlers.pointerUp);
  surface.removeEventListener('pointercancel', handlers.pointerUp);
  surface.removeEventListener('pointerleave', handlers.pointerLeave);
  surface.removeEventListener('focus', handlers.focus);
  surface.removeEventListener('blur', handlers.blur);
  surface.removeEventListener('keydown', handlers.keyDown);
  document.removeEventListener('pointerdown', handlers.outsidePointerDown, true);
  window.removeEventListener('resize', handlers.resize);
  state.attachedSurface = null;
  state.handlers = null;
}

function selectFromPointer(state, event, pin) {
  const rect = state.surface.getBoundingClientRect();
  if (rect.width <= 0) return;
  const chartX = Math.max(0, Math.min(720, ((event.clientX - rect.left) / rect.width) * 720));
  if (pin) state.pinned = true;
  render(state, nearestByX(state.points, chartX), false);
}

function render(state, point, announce) {
  if (!point || !state.surface || !state.tooltip || !state.crosshair) return;
  state.selectedElapsed = point.elapsed;
  state.visible = true;
  state.crosshair.hidden = false;
  state.tooltip.hidden = false;
  state.root.dataset.inspectionVisible = 'true';

  const percent = Math.max(0, Math.min(100, (point.x / 720) * 100));
  state.crosshair.style.left = `${percent}%`;
  state.time.textContent = formatElapsed(point.elapsed);

  const spokenValues = [];
  state.values.forEach((element, index) => {
    const raw = point.values[index] || '';
    const numeric = Number.parseFloat(raw);
    const hasValue = raw !== '' && Number.isFinite(numeric);
    const decimals = Number.parseInt(element.dataset.chartDecimals || '1', 10);
    const unit = element.dataset.chartUnit || '';
    const formatted = hasValue ? `${numeric.toFixed(decimals)}${unit ? ` ${unit}` : ''}` : '—';
    element.textContent = formatted;
    if (hasValue) spokenValues.push(`${element.dataset.chartLabel}: ${formatted}`);
  });

  positionTooltip(state, point.x);
  if (announce && state.announcement) {
    state.announcement.textContent = `${formatElapsed(point.elapsed)}. ${spokenValues.join('. ')}.`;
  }
}

function positionTooltip(state, chartX) {
  const surfaceRect = state.surface.getBoundingClientRect();
  const width = state.tooltip.offsetWidth;
  const pointPixels = (chartX / 720) * surfaceRect.width;
  const preferred = chartX > 400 ? pointPixels - width - 12 : pointPixels + 12;
  const left = Math.max(8, Math.min(surfaceRect.width - width - 8, preferred));
  state.tooltip.style.left = `${Math.max(8, left)}px`;
  state.tooltip.dataset.side = chartX > 400 ? 'left' : 'right';
}

function hide(state) {
  state.visible = false;
  if (state.crosshair) state.crosshair.hidden = true;
  if (state.tooltip) state.tooltip.hidden = true;
  if (state.root) delete state.root.dataset.inspectionVisible;
}

function nearestByX(points, x) {
  let best = points[0];
  let distance = Math.abs(best.x - x);
  for (let index = 1; index < points.length; index += 1) {
    const nextDistance = Math.abs(points[index].x - x);
    if (nextDistance >= distance) continue;
    best = points[index];
    distance = nextDistance;
  }
  return best;
}

function nearestByElapsed(points, elapsed) {
  return points[nearestElapsedIndex(points, elapsed)];
}

function nearestElapsedIndex(points, elapsed) {
  let best = 0;
  let distance = Math.abs(points[0].elapsed - elapsed);
  for (let index = 1; index < points.length; index += 1) {
    const nextDistance = Math.abs(points[index].elapsed - elapsed);
    if (nextDistance >= distance) continue;
    best = index;
    distance = nextDistance;
  }
  return best;
}

function formatElapsed(seconds) {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
    : `${minutes}:${String(remainder).padStart(2, '0')}`;
}
