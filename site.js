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
        typeof dialog.close !== 'function' || !image || !caption ||
        !source || !original || !close || !previous || !next || !count) {
      return;
    }

    const links = Array.from(document.querySelectorAll('a.evidence-image[href]'));
    const uniqueLinks = [];
    const indexByURL = new Map();
    for (const link of links) {
      if (!indexByURL.has(link.href)) {
        indexByURL.set(link.href, uniqueLinks.length);
        uniqueLinks.push(link);
      }
    }
    if (!uniqueLinks.length) return;

    let currentIndex = 0;
    let returnFocus = null;
    count.setAttribute('aria-live', 'polite');
    count.setAttribute('aria-atomic', 'true');
    previous.disabled = next.disabled = uniqueLinks.length < 2;
    original.target = '_blank';
    original.rel = 'noopener noreferrer';
    source.target = '_blank';
    source.rel = 'noopener noreferrer';

    function display(link) {
      currentIndex = indexByURL.get(link.href);
      const description = link.dataset.caption || '';
      const label = link.dataset.label || 'Original report';
      image.alt = link.querySelector('img')?.alt || description || 'Report screenshot';
      image.src = link.href;
      caption.textContent = description;
      original.href = link.href;
      original.setAttribute('aria-label', 'Open full-size image in a new tab');
      const reportURL = link.dataset.source;
      source.hidden = !reportURL;
      if (reportURL) {
        source.href = reportURL;
        source.setAttribute('aria-label', `${label} on CleanApp, opens in a new tab`);
      } else {
        source.removeAttribute('href');
      }
      count.textContent = `${currentIndex + 1} / ${uniqueLinks.length}`;
    }

    function move(direction) {
      if (!dialog.open) return;
      const index = (currentIndex + direction + uniqueLinks.length) % uniqueLinks.length;
      display(uniqueLinks[index]);
    }

    for (const link of links) {
      link.addEventListener('click', (event) => {
        if (event.defaultPrevented || event.button !== 0 ||
            event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        try {
          display(link);
          dialog.showModal();
        } catch {
          // The original image link remains usable if modal support fails.
          return;
        }
        event.preventDefault();
        returnFocus = link;
        close.focus({ preventScroll: true });
      });
    }

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
          event.clientY < bounds.top || event.clientY > bounds.bottom) {
        dialog.close();
      }
    });

    dialog.addEventListener('keydown', (event) => {
      if (!dialog.open || event.defaultPrevented || event.isComposing ||
          event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const target = event.target;
      if (target instanceof Element &&
          target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])')) return;
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
    });
  }

  function enableReveals() {
    const elements = Array.from(document.querySelectorAll('.reveal'));
    if (!elements.length) return;
    const reducedMotion = typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-reduced-motion: reduce)')
      : null;
    const showAll = () => {
      elements.forEach((element) => element.classList.add('is-visible'));
      document.documentElement.classList.remove('reveal-enabled');
    };
    if (reducedMotion?.matches || !('IntersectionObserver' in window)) {
      showAll();
      return;
    }

    let observer;
    let safetyTimer;
    function finish() {
      window.clearTimeout(safetyTimer);
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
      // Motion is decoration: no content stays hidden if observation stalls.
      safetyTimer = window.setTimeout(finish, 5000);
      reducedMotion?.addEventListener?.('change', (event) => {
        if (event.matches) finish();
      });
    } catch {
      finish();
    }
  }

  function initialize() {
    enableImageViewer();
    enableReveals();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize, { once: true });
  } else {
    initialize();
  }
})();
