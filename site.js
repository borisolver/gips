(() => {
  'use strict';

  function enableImageViewer() {
    const dialog = document.getElementById('image-viewer');
    const image = document.getElementById('viewer-image');
    const caption = document.getElementById('viewer-caption');
    const source = document.getElementById('viewer-source');
    const original = document.getElementById('viewer-original');
    const close = document.getElementById('viewer-close');
    const previous = document.getElementById('viewer-prev');
    const next = document.getElementById('viewer-next');
    const count = document.getElementById('viewer-count');
    if (!dialog || typeof dialog.showModal !== 'function' ||
        !image || !caption || !source || !original || !close || !previous || !next || !count) return;

    let uniqueLinks = [];
    let indexByURL = new Map();
    let currentIndex = 0;
    let returnFocus = null;
    count.setAttribute('aria-live', 'polite');
    count.setAttribute('aria-atomic', 'true');
    original.target = source.target = '_blank';
    original.rel = source.rel = 'noopener noreferrer';

    function refresh() {
      const currentURL = uniqueLinks[currentIndex]?.href;
      uniqueLinks = [];
      indexByURL = new Map();
      for (const link of document.querySelectorAll('a.evidence-image[href]')) {
        if (!indexByURL.has(link.href)) {
          indexByURL.set(link.href, uniqueLinks.length);
          uniqueLinks.push(link);
        }
      }
      currentIndex = indexByURL.get(currentURL) ?? 0;
      previous.disabled = next.disabled = uniqueLinks.length < 2;
    }

    function display(link) {
      currentIndex = indexByURL.get(link.href) ?? 0;
      const description = link.dataset.caption || '';
      const label = link.dataset.label || 'Original report';
      image.alt = link.querySelector('img')?.alt || description || 'Report screenshot';
      image.src = link.href;
      caption.textContent = description;
      original.href = link.href;
      original.setAttribute('aria-label', 'Open full-size image in a new tab');
      source.hidden = !link.dataset.source;
      if (link.dataset.source) {
        source.href = link.dataset.source;
        source.setAttribute('aria-label', `${label} on CleanApp, opens in a new tab`);
      } else source.removeAttribute('href');
      count.textContent = `${currentIndex + 1} / ${uniqueLinks.length}`;
    }

    function move(direction) {
      if (!dialog.open || !uniqueLinks.length) return;
      display(uniqueLinks[(currentIndex + direction + uniqueLinks.length) % uniqueLinks.length]);
    }

    // Delegation also covers evidence added by the same-origin update feed.
    document.addEventListener('click', (event) => {
      const link = event.target instanceof Element ? event.target.closest('a.evidence-image[href]') : null;
      if (!link || event.defaultPrevented || event.button !== 0 ||
          event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      refresh();
      try {
        display(link);
        dialog.showModal();
      } catch {
        return; // Keep the ordinary image link if modal support fails.
      }
      event.preventDefault();
      returnFocus = link;
      close.focus({ preventScroll: true });
    });
    document.addEventListener('gips:content-updated', refresh);
    close.addEventListener('click', () => dialog.close());
    previous.addEventListener('click', () => move(-1));
    next.addEventListener('click', () => move(1));
    dialog.addEventListener('cancel', (event) => {
      event.preventDefault();
      dialog.close();
    });
    dialog.addEventListener('click', (event) => {
      if (event.target !== dialog) return;
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right ||
          event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    });
    dialog.addEventListener('keydown', (event) => {
      if (!dialog.open || event.defaultPrevented || event.isComposing ||
          event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (event.target instanceof Element &&
          event.target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])')) return;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault();
        move(event.key === 'ArrowLeft' ? -1 : 1);
      }
    });
    dialog.addEventListener('close', () => {
      image.removeAttribute('src');
      const target = returnFocus;
      returnFocus = null;
      if (target?.isConnected) target.focus({ preventScroll: true });
      document.dispatchEvent(new Event('gips:viewer-closed'));
    });
    refresh();
  }

  function enableLiveList() {
    const list = document.getElementById('proposal-list');
    const spotlight = document.getElementById('priority-spotlight');
    const sortControls = document.querySelector('.sort-controls');
    const status = document.getElementById('list-status');
    const checked = document.getElementById('last-checked');
    const notice = document.getElementById('feed-notice');
    const applyButton = document.getElementById('apply-updates');
    if (!list || !sortControls || !status || !checked || !notice || !applyButton) return;
    let sortMode = 'urgent';
    let version = list.dataset.feedVersion;
    let pending = null;
    let fetching = false;
    let items = new Map(Array.from(list.querySelectorAll('article.proposal')).map((node) => [node.dataset.proposalId, {
      id: node.dataset.proposalId,
      number: Number(node.dataset.number),
      created_at: node.dataset.createdAt,
      updated_at: node.dataset.updatedAt,
      priority_score: Number(node.dataset.priorityScore),
      html_hash: node.dataset.contentHash,
      node,
    }]));
    sortControls.hidden = false;
    const dateValue = (value) => Date.parse(value) || 0;
    let latestCheck = dateValue(checked.dateTime);
    const ordered = (values) => Array.from(values).sort((a, b) => {
      if (sortMode === 'newest') {
        return dateValue(b.created_at) - dateValue(a.created_at) || b.number - a.number;
      }
      return b.priority_score - a.priority_score || dateValue(b.updated_at) - dateValue(a.updated_at) ||
        (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
    });
    const statusText = () => `${items.size} proposals · ${sortMode === 'newest' ? 'Newest first' : 'Most urgent first'}`;

    function setStatus(prefix = '') {
      status.textContent = prefix + statusText();
      for (const button of sortControls.querySelectorAll('[data-sort]')) {
        button.setAttribute('aria-pressed', String(button.dataset.sort === sortMode));
      }
    }

    sortControls.addEventListener('click', (event) => {
      const button = event.target instanceof Element ? event.target.closest('button[data-sort]') : null;
      if (!button || !['urgent', 'newest'].includes(button.dataset.sort)) return;
      sortMode = button.dataset.sort;
      for (const item of ordered(items.values())) list.appendChild(item.node);
      setStatus();
      document.dispatchEvent(new Event('gips:content-updated'));
      tryApply();
    });

    function isInteracting() {
      if (document.querySelector('dialog[open]')) return true;
      const active = document.activeElement;
      if (active && (list.contains(active) || spotlight?.contains(active))) return true;
      const selection = window.getSelection();
      return !!(selection && !selection.isCollapsed &&
        (list.contains(selection.anchorNode) || spotlight?.contains(selection.anchorNode)));
    }

    function parseMarkup(markup, selector) {
      if (typeof markup !== 'string') throw new Error('Missing rendered content');
      const parsed = new DOMParser().parseFromString(markup, 'text/html');
      const node = parsed.body.firstElementChild;
      if (!node?.matches(selector) || parsed.body.children.length !== 1 ||
          parsed.querySelector('script, iframe, object, embed, base, style, link, form')) {
        throw new Error('Invalid rendered content');
      }
      for (const element of [node, ...node.querySelectorAll('*')]) {
        for (const attribute of element.attributes) {
          if (/^on/i.test(attribute.name)) throw new Error('Unexpected executable attribute');
        }
      }
      return document.importNode(node, true);
    }

    function readingAnchor() {
      for (const node of list.children) {
        const bounds = node.getBoundingClientRect();
        if (bounds.bottom > 0 && bounds.top < window.innerHeight) return { id: node.id, top: bounds.top };
      }
      return null;
    }

    function applyFeed(feed) {
      if (!Array.isArray(feed.items) || typeof feed.version !== 'string') throw new Error('Invalid feed');
      const nextItems = new Map();
      for (const item of feed.items) {
        if (!/^GIP-\d+$/.test(item.id) || !Number.isInteger(item.number) || item.number < 1 ||
            !Number.isFinite(item.priority_score) || nextItems.has(item.id)) throw new Error('Invalid proposal');
        const previous = items.get(item.id);
        let node;
        if (previous && item.html_hash && previous.html_hash === item.html_hash) {
          node = previous.node;
        } else {
          node = parseMarkup(item.html, 'article.proposal');
          if (node.id !== item.id.toLowerCase() || node.dataset.proposalId !== item.id) throw new Error('Mismatched proposal');
          node.classList.add('is-visible', 'is-updated');
          if (previous?.node.querySelector('.sources[open]')) {
            const details = node.querySelector('.sources');
            if (details) details.open = true;
          }
        }
        nextItems.set(item.id, { ...item, node });
      }
      let nextSpotlight = null;
      if (spotlight && feed.spotlight_hash !== spotlight.dataset.contentHash) {
        nextSpotlight = parseMarkup(feed.spotlight_html, '.priority-spotlight-content');
      }
      const anchor = readingAnchor();
      list.replaceChildren(...ordered(nextItems.values()).map((item) => item.node));
      if (nextSpotlight) {
        spotlight.replaceChildren(nextSpotlight);
        spotlight.dataset.contentHash = feed.spotlight_hash;
      }
      items = nextItems;
      version = feed.version;
      latestCheck = Math.max(latestCheck, dateValue(feed.checked_at));
      list.dataset.feedVersion = version;
      for (const element of document.querySelectorAll('[data-count]')) {
        const value = feed.counts?.[element.dataset.count];
        if (Number.isFinite(value)) element.textContent = String(value);
      }
      if (feed.checked_at && Number.isFinite(Date.parse(feed.checked_at))) {
        checked.dateTime = feed.checked_at;
        checked.textContent = new Intl.DateTimeFormat(undefined, {
          day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
        }).format(new Date(feed.checked_at));
      }
      setStatus('Updated · ');
      document.dispatchEvent(new Event('gips:content-updated'));
      if (anchor) {
        const replacement = document.getElementById(anchor.id);
        if (replacement) {
          const delta = replacement.getBoundingClientRect().top - anchor.top;
          if (Math.abs(delta) > 1) {
            const oldBehavior = document.documentElement.style.scrollBehavior;
            document.documentElement.style.scrollBehavior = 'auto';
            window.scrollBy(0, delta);
            document.documentElement.style.scrollBehavior = oldBehavior;
          }
        }
      }
    }

    function tryApply() {
      if (!pending || isInteracting()) return;
      try {
        applyFeed(pending);
        pending = null;
        notice.hidden = true;
        if (document.activeElement === applyButton) {
          sortControls.querySelector('[aria-pressed="true"]')?.focus({ preventScroll: true });
        }
      } catch {
        // An incomplete or invalid snapshot never replaces the working page.
        pending = null;
        notice.hidden = true;
      }
    }

    applyButton.addEventListener('click', () => {
      // Focus is outside the replaceable list, so the user's explicit request is safe.
      window.getSelection()?.removeAllRanges();
      tryApply();
    });
    document.addEventListener('focusout', () => window.setTimeout(tryApply, 0));
    document.addEventListener('selectionchange', () => {
      if (window.getSelection()?.isCollapsed) tryApply();
    });
    document.addEventListener('gips:viewer-closed', () => window.setTimeout(tryApply, 0));

    async function pollFeed() {
      if (fetching || document.hidden || !('fetch' in window)) return;
      const url = new URL('feed.json', document.baseURI);
      if (url.origin !== window.location.origin || !/^https?:$/.test(url.protocol)) return;
      fetching = true;
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(), 12000);
      try {
        const response = await fetch(url.href, { cache: 'no-store', credentials: 'omit', signal: controller.signal });
        if (!response.ok || new URL(response.url).origin !== window.location.origin) return;
        const feed = await response.json();
        if (!feed.version || feed.version === version || feed.version === pending?.version) return;
        if (dateValue(feed.checked_at) < Math.max(latestCheck, dateValue(pending?.checked_at))) return;
        pending = feed;
        notice.hidden = false;
        tryApply();
      } catch {
        // The last successful snapshot and its timestamp remain visible offline.
      } finally {
        window.clearTimeout(timer);
        fetching = false;
      }
    }
    window.setInterval(pollFeed, 60000);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) pollFeed();
    });
    window.addEventListener('online', pollFeed);
    pollFeed();
  }

  function enableReveals() {
    const elements = Array.from(document.querySelectorAll('.reveal'));
    if (!elements.length) return;
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    const showAll = () => {
      document.querySelectorAll('.reveal').forEach((element) => element.classList.add('is-visible'));
      document.documentElement.classList.remove('reveal-enabled');
    };
    if (reducedMotion?.matches || !('IntersectionObserver' in window)) {
      showAll();
      return;
    }
    let observer;
    let timer;
    function finish() {
      window.clearTimeout(timer);
      observer?.disconnect();
      showAll();
    }
    try {
      observer = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        }
      }, { threshold: 0, rootMargin: '0px 0px -24px 0px' });
      elements.forEach((element) => observer.observe(element));
      document.documentElement.classList.add('reveal-enabled');
      timer = window.setTimeout(finish, 5000);
      reducedMotion?.addEventListener?.('change', (event) => {
        if (event.matches) finish();
      });
    } catch {
      finish();
    }
  }

  function initialize() {
    enableImageViewer();
    enableLiveList();
    enableReveals();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, { once: true });
  else initialize();
})();
