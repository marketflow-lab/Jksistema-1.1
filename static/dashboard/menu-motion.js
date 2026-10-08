(function () {
  'use strict';

  const preferenceKey = 'jk_dashboard_icon_motion_paused';

  function start() {
    const menu = document.querySelector('.dashboard-menu');
    const root = document.getElementById('cards');
    const feedback = document.getElementById('dashboard-menu-feedback');
    const icons = window.JKDashboardMenuIcons;
    if (!menu || !root || !feedback || !icons) return;

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const entries = new Map();
    const ambient = new Set();
    let phase = 0;
    let userPaused = false;
    let stopped = false;
    try { userPaused = localStorage.getItem(preferenceKey) === '1'; } catch (_) {}

    const meta = document.createElement('div');
    meta.className = 'dashboard-menu-motion-meta';
    feedback.parentNode.insertBefore(meta, feedback);
    meta.appendChild(feedback);
    const toggle = document.createElement('button');
    toggle.id = 'dashboard-menu-motion-toggle';
    toggle.type = 'button';
    toggle.className = 'dashboard-menu-motion-toggle';
    meta.appendChild(toggle);

    function running() {
      return !stopped && !userPaused && !reduced.matches && !document.hidden;
    }

    function updateCard(entry) {
      const active = !reduced.matches && entry.visible &&
        (ambient.has(entry.card) || entry.hovered || document.activeElement === entry.card);
      entry.stage.classList.toggle('motion-active', active);
      entry.card.classList.toggle('dashboard-menu-motion-active', active);
    }

    function resetTilt(entry) {
      entry.card.style.removeProperty('--menu-motion-x');
      entry.card.style.removeProperty('--menu-motion-y');
    }

    function updateState() {
      menu.dataset.motionPaused = String(userPaused);
      menu.dataset.motionHidden = String(document.hidden);
      menu.dataset.motionReduced = String(reduced.matches);
      toggle.disabled = reduced.matches;
      toggle.setAttribute('aria-pressed', String(userPaused || reduced.matches));
      toggle.textContent = reduced.matches ? 'Movimento reduzido' :
        userPaused ? 'Retomar ícones' : 'Pausar ícones';
      toggle.title = reduced.matches ? 'Animações desativadas pela preferência de movimento reduzido.' :
        userPaused ? 'Ativar animações dos ícones' : 'Pausar animações dos ícones';
      if (reduced.matches) entries.forEach(entry => { resetTilt(entry); updateCard(entry); });
    }

    function cycle() {
      if (!running()) return;
      const visible = Array.from(entries.values()).filter(entry => entry.visible);
      ambient.clear();
      const batches = Math.min(4, visible.length);
      if (batches) {
        visible.forEach((entry, index) => {
          if (index % batches === phase % batches) ambient.add(entry.card);
        });
        phase += 1;
      }
      entries.forEach(updateCard);
    }

    const intersection = typeof IntersectionObserver === 'function' ?
      new IntersectionObserver(changes => {
        changes.forEach(change => {
          const entry = entries.get(change.target);
          if (!entry) return;
          entry.visible = change.isIntersecting;
          entry.card.dataset.motionVisible = String(entry.visible);
          if (running() || reduced.matches) updateCard(entry);
        });
      }) : null;

    function mount(card) {
      const artwork = icons[card.dataset.key];
      const original = card.querySelector('.dashboard-menu-illustration');
      if (!artwork || !original) return;
      const stage = document.createElement('span');
      stage.className = 'dashboard-menu-motion-stage';
      stage.setAttribute('aria-hidden', 'true');
      const halo = document.createElement('span');
      halo.className = 'dashboard-menu-motion-halo';
      const object = document.createElement('span');
      object.className = 'dashboard-menu-motion-object';
      // These SVGs are fixed, bundled artwork; no account or API content is inserted.
      object.innerHTML = artwork;
      stage.appendChild(halo);
      stage.appendChild(object);
      original.replaceWith(stage);
      const rect = card.getBoundingClientRect();
      const entry = { card, stage, hovered: false,
        visible: rect.bottom > 0 && rect.top < window.innerHeight, listeners: [] };
      entries.set(card, entry);
      card.dataset.motionVisible = String(entry.visible);
      function listen(name, handler) {
        card.addEventListener(name, handler);
        entry.listeners.push([name, handler]);
      }
      listen('pointerenter', () => { entry.hovered = true; if (running()) updateCard(entry); });
      listen('pointerleave', () => {
        entry.hovered = false;
        if (running()) { resetTilt(entry); updateCard(entry); }
      });
      listen('pointermove', event => {
        if (!running() || event.pointerType === 'touch') return;
        const bounds = card.getBoundingClientRect();
        if (!bounds.width || !bounds.height) return;
        card.style.setProperty('--menu-motion-y', String(((event.clientX - bounds.left) / bounds.width - .5) * 9) + 'deg');
        card.style.setProperty('--menu-motion-x', String(-((event.clientY - bounds.top) / bounds.height - .5) * 6) + 'deg');
      });
      listen('focus', () => { if (running()) updateCard(entry); });
      listen('blur', () => { if (running()) updateCard(entry); });
      if (intersection) intersection.observe(card);
    }

    function unmount(entry) {
      entry.listeners.forEach(([name, handler]) => entry.card.removeEventListener(name, handler));
      if (intersection) intersection.unobserve(entry.card);
      ambient.delete(entry.card);
      entry.stage.classList.remove('motion-active');
      entry.card.classList.remove('dashboard-menu-motion-active');
      resetTilt(entry);
      entries.delete(entry.card);
    }

    function sync() {
      if (stopped) return;
      const cards = new Set(root.querySelectorAll('.card.dashboard-menu-module'));
      entries.forEach(entry => { if (!cards.has(entry.card)) unmount(entry); });
      cards.forEach(card => { if (!entries.has(card)) mount(card); });
      updateState();
      cycle();
    }

    function resume() {
      entries.forEach(entry => {
        resetTilt(entry);
        updateCard(entry);
      });
      cycle();
    }

    function toggleMotion() {
      if (reduced.matches) return;
      userPaused = !userPaused;
      try { localStorage.setItem(preferenceKey, userPaused ? '1' : '0'); } catch (_) {}
      updateState();
      if (!userPaused) resume();
    }

    function onReducedChange() {
      ambient.clear();
      updateState();
      if (running()) resume();
    }

    function onVisibilityChange() {
      updateState();
    }

    function onStorage(event) {
      if (event.key !== preferenceKey) return;
      userPaused = event.newValue === '1';
      updateState();
      if (!userPaused) resume();
    }

    const mutations = new MutationObserver(sync);
    mutations.observe(root, { childList: true });
    toggle.addEventListener('click', toggleMotion);
    reduced.addEventListener('change', onReducedChange);
    document.addEventListener('visibilitychange', onVisibilityChange);
    window.addEventListener('storage', onStorage);
    const timer = window.setInterval(cycle, 5200);

    function destroy() {
      if (stopped) return;
      stopped = true;
      window.clearInterval(timer);
      mutations.disconnect();
      if (intersection) intersection.disconnect();
      Array.from(entries.values()).forEach(unmount);
      reduced.removeEventListener('change', onReducedChange);
      document.removeEventListener('visibilitychange', onVisibilityChange);
      toggle.removeEventListener('click', toggleMotion);
      window.removeEventListener('storage', onStorage);
      window.removeEventListener('pagehide', onPageHide);
      window.removeEventListener('pageshow', onPageShow);
    }

    function onPageHide(event) {
      if (event.persisted) menu.dataset.motionHidden = 'true';
      else destroy();
    }

    function onPageShow(event) {
      if (event.persisted) { updateState(); sync(); }
    }

    window.addEventListener('pagehide', onPageHide);
    window.addEventListener('pageshow', onPageShow);
    sync();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
